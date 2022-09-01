import logging

import numpy as np
import pandas as pd
from numpy.typing import ArrayLike
from scipy import sparse
from skimage.util._map_array import ArrayMap

from ultrack.config.config import TrackingConfig
from ultrack.core.database import NO_PARENT
from ultrack.core.tracking.solver.base_solver import BaseSolver
from ultrack.core.tracking.solver.heuristic._heap import Heap

LOG = logging.getLogger(__name__)


class HeuristicSolver(BaseSolver):
    def __init__(
        self,
        config: TrackingConfig,
    ) -> None:
        # TODO
        self._config = config
        self._rng = np.random.default_rng(42)

        # w_ap, w_dap, w_div
        self._add_in_map = np.full((2, 3, 3), fill_value=-1e6, dtype=np.float32)
        self._add_in_map[0, 0] = (0, 1, 0)
        self._add_in_map[0, 1:] = (-1, 0, 0)

        self._sub_in_map = -np.roll(self._add_in_map, shift=1, axis=0)

        self._add_out_map = np.full((2, 3, 3), fill_value=-1e6, dtype=np.float32)
        self._add_out_map[0, 0] = (1, 0, 0)
        self._add_out_map[1, 0] = (0, -1, 0)
        self._add_out_map[:, 1] = (0, 0, 1)

        self._sub_out_map = -np.roll(self._add_out_map, shift=1, axis=1)

    def add_nodes(
        self, indices: ArrayLike, is_first_t: ArrayLike, is_last_t: ArrayLike
    ) -> None:
        """Add nodes to heuristic model.

        Parameters
        ----------
        indices : ArrayLike
            Nodes indices.
        is_first_t : ArrayLike
            Boolean array indicating if it belongs to first time point and it won't receive appearance penalization.
        is_last_t : ArrayLike
            Boolean array indicating if it belongs to last time point and it won't receive disappearance penalization.
        """
        # TODO assert it can't be reconfigured
        self._assert_same_length(
            indices=indices, is_first_t=is_first_t, is_last_t=is_last_t
        )

        size = len(indices)
        self._forward_map = ArrayMap(indices, np.arange(size))
        self._backward_map = indices.copy()

        self._appear_weight = np.logical_not(is_first_t) * self._config.appear_weight
        self._disappear_weight = (
            np.logical_not(is_last_t) * self._config.disappear_weight
        )
        self._forbidden = np.zeros(size, dtype=bool)

    def add_edges(
        self, sources: ArrayLike, targets: ArrayLike, weights: ArrayLike
    ) -> None:
        """Add edges to model and applies weights link function from config.

        Parameters
        ----------
        source : ArrayLike
            Array of integers indicating source indices.
        targets : ArrayLike
            Array of integers indicating target indices.
        weights : ArrayLike
            Array of weights, input to the link function.
        """
        # TODO assert it can't be reconfigured
        self._assert_same_length(weights=weights, sources=sources, targets=targets)

        self._weights = self._config.apply_link_function(weights).astype(np.float32)
        self._out_edge = self._forward_map[np.asarray(sources)]
        self._in_edge = self._forward_map[np.asarray(targets)]

        LOG.info(f"transformed edge weights {self._weights}")

    def add_overlap_constraints(self, source: ArrayLike, target: ArrayLike) -> None:
        """Add constraints such that `source` and `target` can't be present in the same solution.

        Parameters
        ----------
        source : ArrayLike
            Source nodes indices.
        target : ArrayLike
            Target nodes indices.
        """
        source = self._forward_map[np.asarray(source)]
        target = self._forward_map[np.asarray(target)]
        mask = np.ones(len(source), dtype=bool)
        size = len(self._appear_weight)
        self._overlap = sparse.csr_matrix(
            (mask, (source, target)), shape=(size, size), dtype=bool
        )

    def enforce_node_to_solution(self, indices: ArrayLike) -> None:
        """Constraints given nodes' variables to 1.

        Parameters
        ----------
        indices : ArrayLike
            Nodes indices.
        """
        indices = self._forward_map[np.asarray(indices)]
        for i in indices:
            self._forbid_overlap(i)

    def optimize(self) -> float:
        """Optimizes objective function."""
        n_nodes = len(self._appear_weight)

        self._in_count = np.zeros(n_nodes, dtype=np.uint8)
        self._out_count = np.zeros(n_nodes, dtype=np.uint8)
        self._selected_nodes = np.zeros(n_nodes, dtype=bool)
        self._selected_edges = set()

        objective = self.mst_pass()
        # objective = self.random_ascent(objective)

        return objective

    def _transition_weight(self, transition: np.ndarray, node_id: int) -> float:
        # TODO
        weights = (
            self._appear_weight[node_id],
            self._disappear_weight[node_id],
            self._config.division_weight,
        )
        return np.sum(
            transition[self._in_count[node_id], self._out_count[node_id]] * weights
        )

    def mst_pass(self, objective: float = 0.0) -> float:

        heap = Heap(self._weights)
        heap.insert_array(np.arange(len(self._weights)))

        while not heap.is_empty():
            edge_index = heap.pop()
            in_node = self._in_edge[edge_index]
            out_node = self._out_edge[edge_index]

            # blocked by overlap
            if self._forbidden[in_node] or self._forbidden[out_node]:
                continue

            # check for flow constrains
            if self._in_count[in_node] > 0 or self._out_count[out_node] > 1:
                continue

            obj_delta = self._weights[edge_index]
            obj_delta += self._transition_weight(self._add_in_map, in_node)
            obj_delta += self._transition_weight(self._add_out_map, out_node)

            # any other check?
            objective = objective + obj_delta

            self._selected_edges.add((out_node, in_node))
            self._selected_nodes[out_node] = True
            self._selected_nodes[in_node] = True

            self._out_count[out_node] += 1
            self._in_count[in_node] += 1
            self._forbid_overlap(in_node)
            self._forbid_overlap(out_node)

        return objective

    def random_ascent(self, objective: float) -> float:
        n_nodes = len(self._appear_weight)

        self._forward_graph = sparse.csr_matrix(
            (self._weights, (self._out_edge, self._in_edge)),
            shape=(n_nodes, n_nodes),
        )
        self._backward_graph = sparse.csr_matrix(
            (self._weights, (self._in_edge, self._out_edge)),
            shape=(n_nodes, n_nodes),
        )

        self._forward_selected = np.full_like(n_nodes, -1, dtype=int)
        self._backward_selected = np.full_like(n_nodes, -1, dtype=int)

        selected = np.nonzero(self._selected_edges)
        self._forward_selected[self._out_edge[selected]] = selected
        self._backward_selected[self._in_edge[selected]] = selected

        node_ids = self._rng.choice(n_nodes, size=n_nodes, replace=False)
        for node_id in node_ids:
            pass
            # objective += self._local_move_backward(node_id)
            # objective += self._local_move_forward(node_id)

        return objective

    def _forbid_overlap(self, node_index: int) -> None:
        for i in range(
            self._overlap.indptr[node_index], self._overlap.indptr[node_index + 1]
        ):
            self._forbidden[self._overlap.indices[i]] = True

    def solution(self) -> pd.DataFrame:
        """Returns the nodes present on the solution.

        Returns
        -------
        pd.DataFrame
            Dataframe indexed by nodes as indices and their parent (NA if orphan).
        """
        nodes = self._backward_map[np.nonzero(self._selected_nodes)[0]]

        nodes = pd.DataFrame(
            data=NO_PARENT,
            index=nodes,
            columns=["parent_id"],
        )

        parent_id, node_id = zip(*self._selected_edges)

        node_id = self._backward_map[np.asarray(node_id)]
        parent_id = self._backward_map[np.asarray(parent_id)]

        inv_edges = pd.DataFrame(
            data=parent_id,
            index=node_id,
            columns=["parent_id"],
        )

        nodes.update(inv_edges)
        return nodes

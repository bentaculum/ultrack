import os
import warnings

if os.environ.get("ULTRACK_DEBUG", False):
    import logging

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

# ignoring small float32/64 zero flush warning
warnings.filterwarnings("ignore", message="The value of the smallest subnormal for")

from importlib.metadata import version as _version

__version__ = _version(__name__)

from ultrack.config.config import MainConfig, load_config
from ultrack.core.export.ctc import to_ctc
from ultrack.core.export.trackmate import to_trackmate
from ultrack.core.export.tracks_layer import to_tracks_layer
from ultrack.core.export.zarr import tracks_to_zarr
from ultrack.core.linking.processing import link
from ultrack.core.main import track
from ultrack.core.segmentation.processing import segment
from ultrack.core.solve.processing import solve
from ultrack.imgproc.flow import add_flow

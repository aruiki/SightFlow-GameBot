"""sightflow: object detectors + human-written rule flows for game automation."""
from .bot import Bot, load_config
from .detection import Detection, LabelGroup
from .detectors import Detector, register
from .flow import Flow

__all__ = ["Bot", "load_config", "Detection", "LabelGroup", "Detector", "register", "Flow"]
__version__ = "0.1.0"

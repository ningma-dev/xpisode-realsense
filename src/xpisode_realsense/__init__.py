"""RealSense cameras as xpisode device peers (the xpisode device protocol).

One model is supported, the D405 (``xpisode_realsense:D405``). ``camera`` holds what does
not depend on the model; a model's module holds what does.
"""

from xpisode_realsense.camera import Newest
from xpisode_realsense.d405 import D405

__all__ = ["D405", "Newest"]

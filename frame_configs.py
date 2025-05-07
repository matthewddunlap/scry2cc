"""
Frame configurations module
Imports and provides access to different frame configurations
"""
from seventh_frame import SEVENTH_FRAME
from eighth_frame import EIGHTH_FRAME
from m15_frame import M15_FRAME

def get_frame_config(frame_type: str):
    """Get the configuration for the specified frame type."""
    if frame_type == "8th":
        return EIGHTH_FRAME
    elif frame_type == "m15":
        return M15_FRAME
    else:
        return SEVENTH_FRAME  # Default to seventh

"""
Frame configurations module
Imports and provides access to different frame configurations
"""
import logging 

from seventh_frame import SEVENTH_FRAME
from eighth_frame import EIGHTH_FRAME
from m15_frame import M15_FRAME
from m15regularnew_frame import M15_REGULAR_NEW_FRAME
from modern_frame import MODERN_FRAME
from exceptions import FrameGenerationException
try:
    from m15ub_frame import M15UB_FRAME 
    logger_fc = logging.getLogger(__name__) 
    logger_fc.info("Successfully imported M15UB_FRAME from m15ub_frame.py")
except ImportError as e:
    logger_fc = logging.getLogger(__name__)
    logger_fc.error(f"FAILED to import M15UB_FRAME from m15ub_frame.py: {e}")
    M15UB_FRAME = None 

def get_frame_config(frame_type: str):
    """Get the configuration for the specified frame type."""
    logger_fc = logging.getLogger(__name__) 
    logger_fc.debug(f"get_frame_config called with frame_type: '{frame_type}'")

    if frame_type == "8th":
        logger_fc.debug("Returning EIGHTH_FRAME")
        return EIGHTH_FRAME
    elif frame_type == "m15":
        logger_fc.debug("Returning M15_FRAME")
        return M15_FRAME
    elif frame_type == "m15regularnew":
        logger_fc.debug("Returning M15_REGULAR_NEW_FRAME")
        return M15_REGULAR_NEW_FRAME
    elif frame_type == "modern":
        logger_fc.debug("Returning MODERN_FRAME")
        return MODERN_FRAME
    elif frame_type == "m15ub": 
        if M15UB_FRAME is not None:
            logger_fc.debug("Returning M15UB_FRAME")
            return M15UB_FRAME
        else:
            raise FrameGenerationException("M15UB_FRAME was not imported correctly or is None.", "Please check the m15ub_frame.py file.")
    else: 
        logger_fc.debug(f"frame_type '{frame_type}' not matched or unknown, defaulting to SEVENTH_FRAME")
        return SEVENTH_FRAME
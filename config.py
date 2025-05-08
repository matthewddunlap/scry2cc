"""
Configuration module for the MTG Scryfall to CardConjurer Converter
"""
import logging

# Server configuration
ccProto = "http"
ccHost = "mtgproxy"
ccPort = "4242"

# Common default values for all frames
DEFAULT_INFO_YEAR = "2025"
DEFAULT_INFO_RARITY = "P"
DEFAULT_INFO_SET = "MTG"
DEFAULT_INFO_LANGUAGE = "EN"
DEFAULT_INFO_ARTIST = "Unknown Artist"
DEFAULT_INFO_NOTE = ""
DEFAULT_INFO_NUMBER = "2025"

def init_logging():
    """Initialize logging configuration"""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

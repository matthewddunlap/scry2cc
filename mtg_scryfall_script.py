#!/usr/bin/env python3
"""
MTG Scryfall to CardConjurer Converter
Main script file that imports all other modules and runs the conversion process.
"""
import sys
import json
import time
import logging
import argparse
from typing import List, Dict

# Import modules
from config import (
    init_logging,
    ccProto, ccHost, ccPort,
    DEFAULT_INFO_YEAR, DEFAULT_INFO_RARITY, DEFAULT_INFO_SET,
    DEFAULT_INFO_LANGUAGE, DEFAULT_INFO_ARTIST, DEFAULT_INFO_NOTE, DEFAULT_INFO_NUMBER
)
from frame_configs import get_frame_config
from color_mapping import COLOR_CODE_MAP, RARITY_MAP
from scryfall_processor import ScryfallCardProcessor

# Initialize logging
init_logging()
logger = logging.getLogger(__name__)

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(description='Process MTG cards and create CardConjurer JSON')
    parser.add_argument('input_file', help='Path to the input file containing card names')
    parser.add_argument('--output_file', '-o', help='Path to the output JSON file', default='mtg_cards_output.cardconjurer')
    parser.add_argument('--frame', '-f', help='Frame type to use', default='seventh', choices=['seventh', '8th', 'm15'])
    parser.add_argument('--frame_set', '-s', help='Frame set to use (only for seventh)', default='regular')
    parser.add_argument('--legendary_crowns', action='store_true', help='Add legendary crowns for M15 frame (if applicable)') # New
    
    args = parser.parse_args()
    
    # Validate frame type
    if args.frame not in ['seventh', '8th', 'm15']:
        logger.error(f"Invalid frame type: {args.frame}. Must be 'seventh', '8th', or 'm15'")
        sys.exit(1)
    # The 'choices' parameter in argparse handles this validation automatically.
    # If an invalid choice is given, argparse will exit with an error message.
    # So, the explicit if check can be removed if 'choices' is used.
    
    processor = ScryfallCardProcessor(args.input_file, args.frame, args.frame_set, args.legendary_crowns)
    result = processor.process_cards()
    processor.save_output(args.output_file, result)

if __name__ == "__main__":
    main()

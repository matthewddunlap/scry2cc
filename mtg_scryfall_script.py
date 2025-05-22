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

from config import (
    init_logging,
    ccProto, ccHost, ccPort,
    DEFAULT_INFO_YEAR, DEFAULT_INFO_RARITY, DEFAULT_INFO_SET,
    DEFAULT_INFO_LANGUAGE, DEFAULT_INFO_ARTIST, DEFAULT_INFO_NOTE, DEFAULT_INFO_NUMBER, DEFAULT_API_DELAY_MS 
)
from frame_configs import get_frame_config
from scryfall_processor import ScryfallCardProcessor

init_logging()
logger = logging.getLogger(__name__)

def main():
    parser = argparse.ArgumentParser(description='Process MTG cards and create CardConjurer JSON. Provide EITHER an input_file OR --fetch_basic_land.')
    
    parser.add_argument('input_file', nargs='?', default=None, 
                        help='Path to the input file containing card names. Ignored if --fetch_basic_land is used.')
    
    parser.add_argument('--output_file', '-o', help='Path to the output JSON file', default='mtg_cards_output.cardconjurer')
    parser.add_argument('--frame', '-f', help='Frame type to use', default='seventh', choices=['seventh', '8th', 'm15', 'm15ub']) 
    parser.add_argument('--frame_set', '-s', help='Frame set to use (only for seventh)', default='regular')
    parser.add_argument('--legendary_crowns', action='store_true', help='Add legendary crowns for M15/M15UB frames (if applicable)')
    parser.add_argument('--auto_fit_art', action='store_true', help='Automatically calculate art X, Y, and Zoom to fit frame')
    parser.add_argument('--auto_fit_set_symbol', action='store_true', help='Automatically calculate set symbol X, Y, and Zoom to fit bounds')
    parser.add_argument('--set-symbol-override', type=str, default=None, metavar='CODE',
                        help='Override the set symbol using this code (e.g., "myset", "proxy"). Rarity is still used.')
    parser.add_argument('--api_delay_ms', type=int, default=DEFAULT_API_DELAY_MS, 
                        help=f'Delay in milliseconds between Scryfall API calls (default: {DEFAULT_API_DELAY_MS}ms)')
    
    parser.add_argument('--fetch_basic_land', type=str, default=None, 
                        choices=['Forest', 'Island', 'Mountain', 'Plains', 'Swamp'],
                        help='Fetch all non-full-art printings (unique by art) of a specific basic land type. If used, input_file is ignored.')
    
    args = parser.parse_args()

    # --- DEBUG ARGS ---
    print(f"DEBUG: Parsed args: {args}") 
    # --- END DEBUG ---

    if not args.input_file and not args.fetch_basic_land:
        parser.error("Either an input_file or the --fetch_basic_land option must be specified.")
    
    effective_input_file = args.input_file
    if args.fetch_basic_land:
        if args.input_file: 
            logger.warning(f"--fetch_basic_land ('{args.fetch_basic_land}') is specified; input_file ('{args.input_file}') will be ignored.")
        effective_input_file = None 

    calculated_api_delay_seconds = args.api_delay_ms / 1000.0
    if calculated_api_delay_seconds < 0:
        calculated_api_delay_seconds = 0.0

    processor = ScryfallCardProcessor(
        input_file=effective_input_file, 
        frame_type=args.frame, 
        frame_set=args.frame_set, 
        legendary_crowns=args.legendary_crowns, 
        auto_fit_art=args.auto_fit_art,
        set_symbol_override=args.set_symbol_override,
        auto_fit_set_symbol=args.auto_fit_set_symbol,
        api_delay_seconds=calculated_api_delay_seconds,
        fetch_basic_land_type=args.fetch_basic_land 
    )
    result = processor.process_cards()
    processor.save_output(args.output_file, result)

if __name__ == "__main__":
    main()
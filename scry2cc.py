# --- scry2cc.py ---
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
    DEFAULT_API_DELAY_MS 
)
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
    
    # NEW: Art mode argument
    parser.add_argument('--art_mode', type=str, default='earliest', 
                        choices=['earliest', 'latest', 'all_art'],
                        help='Art selection mode: earliest (default), latest, or all_art (all unique art versions)')

    upscaling_group = parser.add_argument_group('Upscaling Options')
    upscaling_group.add_argument('--upscale_art', action='store_true', help='Enable art upscaling via Ilaria Upscaler.')
    upscaling_group.add_argument('--ilaria_base_url', type=str, default=None, help='Base URL of the Ilaria Upscaler (e.g., https://thestinger-ilaria-upscaler.hf.space).')
    upscaling_group.add_argument('--upscaler_model_name', type=str, default="RealESRGAN_x2plus", help='Upscaler model name (default: RealESRGAN_x2plus).')
    upscaling_group.add_argument('--upscaler_outscale_factor', type=int, default=2, help='Upscale factor (default: 2).')
    upscaling_group.add_argument('--upscaler_denoise_strength', type=float, default=0.5, help='Denoise strength (default: 0.5).')
    upscaling_group.add_argument('--upscaler_face_enhance', action='store_true', help='Enable face enhancement (default: False).')

    image_server_group = parser.add_argument_group('Image Server Options')
    image_server_group.add_argument('--image_server_base_url', type=str, default=None, 
                                   help='Base URL of the image server (e.g., http://localhost:8088). Required if --upscale_art is used for hosting.')
    image_server_group.add_argument('--image_server_path_prefix', type=str, default="/local_art",
                                   help='Base path prefix on image server (default: /local_art).')
    
    args = parser.parse_args()
    logger.debug(f"Parsed args: {args}") 

    if not args.input_file and not args.fetch_basic_land:
        parser.error("Either an input_file or --fetch_basic_land must be specified.")
    
    if args.upscale_art and (not args.ilaria_base_url or not args.image_server_base_url):
        parser.error("--ilaria_base_url and --image_server_base_url are required when --upscale_art is enabled.")

    processor = ScryfallCardProcessor(
        input_file=args.input_file if not args.fetch_basic_land else None, 
        frame_type=args.frame, 
        frame_set=args.frame_set, 
        legendary_crowns=args.legendary_crowns, 
        auto_fit_art=args.auto_fit_art,
        set_symbol_override=args.set_symbol_override,
        auto_fit_set_symbol=args.auto_fit_set_symbol,
        api_delay_seconds=max(0, args.api_delay_ms / 1000.0),
        fetch_basic_land_type=args.fetch_basic_land,
        art_mode=args.art_mode,  # NEW: Pass art mode
        
        upscale_art=args.upscale_art,
        ilaria_upscaler_base_url=args.ilaria_base_url,
        upscaler_model_name=args.upscaler_model_name,
        upscaler_outscale_factor=args.upscaler_outscale_factor,
        upscaler_denoise_strength=args.upscaler_denoise_strength,
        upscaler_face_enhance=args.upscaler_face_enhance,
        
        image_server_base_url=args.image_server_base_url,
        image_server_path_prefix=args.image_server_path_prefix
    )
    result = processor.process_cards()
    processor.save_output(args.output_file, result)

if __name__ == "__main__":
    main()

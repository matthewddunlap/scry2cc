"""
Main processor for converting Scryfall card data to CardConjurer format
"""
import sys
import json
import time
import logging
from typing import Dict, List, Optional

from scryfall_api_utils import ScryfallAPI
from color_detector import ColorDetector
from card_builder import CardBuilder
from frame_configs import get_frame_config

logger = logging.getLogger(__name__)

class ScryfallCardProcessor:
    """Main class for processing cards from Scryfall to CardConjurer format"""
    
    def __init__(self, input_file: str, frame_type: str = "seventh", frame_set: str = "regular", legendary_crowns: bool = False, auto_fit_art: bool = False, set_symbol_override: Optional[str] = None, auto_fit_set_symbol: bool = False, api_delay_seconds: float = 0.1):
        self.input_file = input_file
        self.frame_type = frame_type
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol
        self.api_delay_seconds = api_delay_seconds
        self.frame_config = get_frame_config(frame_type)
        self.scryfall_api = ScryfallAPI()  # ScryfallAPI itself doesn't handle delays, the caller does
        self.color_detector = ColorDetector()
        self.card_builder = CardBuilder(frame_type, self.frame_config, frame_set, self.legendary_crowns, self.auto_fit_art, self.set_symbol_override, self.auto_fit_set_symbol)
    
    def load_cards(self) -> List[str]:
        """Load card names from input file."""
        try:
            with open(self.input_file, 'r') as file:
                # Strip whitespace and filter out empty lines
                return [line.strip() for line in file if line.strip()]
        except FileNotFoundError:
            logger.error(f"Input file not found: {self.input_file}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Error reading input file: {e}")
            sys.exit(1)
    
# --- In scryfall_processor.py ---

    def process_cards(self) -> List[Dict]:
        """Process all cards from the input file."""
        cards = self.load_cards()
        if not cards: # If load_cards exited or returned empty
            return []
            
        result = []
        num_cards = len(cards)
        
        for i, card_name in enumerate(cards):
            logger.info(f"Processing card ({i+1}/{num_cards}): {card_name}")
            
            try:
                # --- API CALL 1 (Scryfall card data) ---
                # Assuming get_earliest_printing makes an API call to Scryfall
                card_data = self.scryfall_api.get_earliest_printing(card_name)
                
                # --- APPLY DELAY AFTER THIS API CALL (to Scryfall) ---
                # This delay respects Scryfall's rate limits for the call above.
                # CardBuilder will handle its own delays for its image/SVG fetches.
                if self.api_delay_seconds > 0:
                    logger.debug(f"Delaying for {self.api_delay_seconds * 1000:.0f}ms after Scryfall card data fetch for '{card_name}'...")
                    time.sleep(self.api_delay_seconds)
                # ------------------------------------
                
                if card_data:
                    color_info = ColorDetector.get_color_info(card_data) # Local processing, no API call
                    
                    # CardBuilder.build_card_data internally handles delays for its network requests (art/symbol SVGs)
                    # based on the self.api_delay_seconds it received.
                    card_object = self.card_builder.build_card_data(card_name, card_data, color_info)
                    result.append(card_object)
                    
                    # No additional fixed time.sleep(0.5) or other sleep is needed here.
                    # The next iteration of the loop will apply a delay AFTER its Scryfall API call
                    # if self.api_delay_seconds > 0.
                else:
                    # get_earliest_printing should ideally log if it can't find a card.
                    # This is an additional log if it returns None unexpectedly.
                    logger.error(f"No card data returned from Scryfall API for: {card_name}")
            except Exception as e:
                # Log the full traceback for unexpected errors during processing of a single card
                logger.error(f"Unexpected error processing card '{card_name}': {e}", exc_info=True)
        
        return result
    
    def save_output(self, output_file: str, data: List[Dict]):
        """Save processed data to JSON file."""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Output saved to {output_file}")
        except Exception as e:
            logger.error(f"Error saving output file: {e}")
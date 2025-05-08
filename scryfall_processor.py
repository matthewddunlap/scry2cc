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
    
    def __init__(self, input_file: str, frame_type: str = "seventh", frame_set: str = "regular", legendary_crowns: bool = False, auto_fit_art: bool = False, set_symbol_override: Optional[str] = None, auto_fit_set_symbol: bool = False):
        self.input_file = input_file
        self.frame_type = frame_type
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol
        self.frame_config = get_frame_config(frame_type)
        self.scryfall_api = ScryfallAPI()
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
    
    def process_cards(self) -> List[Dict]:
        """Process all cards from the input file."""
        cards = self.load_cards()
        result = []
        
        for card_name in cards:
            logger.info(f"Processing card: {card_name}")
            
            try:
                # Get the earliest printing of the card
                card_data = self.scryfall_api.get_earliest_printing(card_name)
                
                if card_data:
                    # Detect card color
                    color_info = ColorDetector.get_color_info(card_data)
                    
                    # Build card data object
                    card_object = self.card_builder.build_card_data(card_name, card_data, color_info)
                    result.append(card_object)
                    
                    # Add a delay to avoid rate limiting
                    time.sleep(0.5)
                else:
                    logger.error(f"Failed to process card: {card_name}")
            except Exception as e:
                logger.error(f"Error processing card {card_name}: {e}")
        
        return result
    
    def save_output(self, output_file: str, data: List[Dict]):
        """Save processed data to JSON file."""
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Output saved to {output_file}")
        except Exception as e:
            logger.error(f"Error saving output file: {e}")

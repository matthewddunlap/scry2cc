"""
Main processor for converting Scryfall card data to CardConjurer format
"""
import sys
import json
import time
import logging
import re # Import the 're' module for regular expressions
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
        self.scryfall_api = ScryfallAPI()  
        self.color_detector = ColorDetector() 
        self.card_builder = CardBuilder(frame_type, self.frame_config, frame_set, self.legendary_crowns, self.auto_fit_art, self.set_symbol_override, self.auto_fit_set_symbol, self.api_delay_seconds)
    
    def load_cards(self) -> List[str]:
        """Load card names from input file, stripping leading counts."""
        card_names = []
        try:
            with open(self.input_file, 'r') as file:
                for line in file:
                    # Strip leading/trailing whitespace first
                    processed_line = line.strip()
                    # Use regex to remove leading digits and spaces (e.g., "4 ", "10x ")
                    # This pattern matches:
                    # ^       - start of the string
                    # \d+     - one or more digits
                    # [xX\s]* - zero or more occurrences of 'x', 'X', or whitespace
                    #           (to handle "4x Card Name" or "4 Card Name")
                    # The rest of the line is captured by (.+)
                    match = re.match(r"^\d+[xX\s]*(.+)", processed_line)
                    if match:
                        card_name = match.group(1).strip() # Get the captured card name and strip again
                    else:
                        card_name = processed_line # No leading count found, use the stripped line
                    
                    if card_name: # Only add if not empty after processing
                        card_names.append(card_name)
            
            if not card_names:
                logger.warning(f"No valid card names found in input file: {self.input_file}")
            return card_names
        except FileNotFoundError:
            logger.error(f"Input file not found: {self.input_file}")
            # sys.exit(1) # Consider returning empty list or raising custom exception
            return [] 
        except Exception as e:
            logger.error(f"Error reading input file: {e}")
            # sys.exit(1) # Same as above
            return []
    
    def process_cards(self) -> List[Dict]:
        """Process all cards from the input file."""
        cards = self.load_cards()
        if not cards: 
            return []
            
        result = []
        num_cards = len(cards)
        
        for i, card_name in enumerate(cards):
            logger.info(f"Processing card ({i+1}/{num_cards}): {card_name}")
            
            try:
                card_data = self.scryfall_api.get_earliest_printing(card_name)
                
                if not card_data: 
                    logger.warning(f"No card data retrieved for {card_name}, skipping.")
                    if self.api_delay_seconds > 0 and i < num_cards -1 : 
                         time.sleep(self.api_delay_seconds)
                    continue 

                # Optional: Log raw card_data for deep debugging
                # logger.debug(f"Raw card_data for '{card_name}' from API: {json.dumps(card_data, indent=2)}")

                if self.api_delay_seconds > 0:
                    logger.debug(f"Delaying for {self.api_delay_seconds * 1000:.0f}ms after Scryfall card data fetch for '{card_name}'...")
                    time.sleep(self.api_delay_seconds)
                
                color_info = ColorDetector.get_color_info(card_data) 
                card_object = self.card_builder.build_card_data(card_name, card_data, color_info)
                result.append(card_object)

            except Exception as e:
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
"""
Main processor for converting Scryfall card data to CardConjurer format
"""
import sys
import json
import time
import logging
import re 
from typing import Dict, List, Optional

from scryfall_api_utils import ScryfallAPI 
from color_detector import ColorDetector
from card_builder import CardBuilder
from frame_configs import get_frame_config

logger = logging.getLogger(__name__)

class ScryfallCardProcessor:
    """Main class for processing cards from Scryfall to CardConjurer format"""
    
    def __init__(self, input_file: Optional[str], 
                 frame_type: str = "seventh", 
                 frame_set: str = "regular", 
                 legendary_crowns: bool = False, 
                 auto_fit_art: bool = False, 
                 set_symbol_override: Optional[str] = None, 
                 auto_fit_set_symbol: bool = False, 
                 api_delay_seconds: float = 0.1,
                 fetch_basic_land_type: Optional[str] = None): 
        
        self.input_file = input_file
        self.frame_type = frame_type
        self.frame_set = frame_set
        self.legendary_crowns = legendary_crowns
        self.auto_fit_art = auto_fit_art
        self.set_symbol_override = set_symbol_override
        self.auto_fit_set_symbol = auto_fit_set_symbol
        self.api_delay_seconds = api_delay_seconds
        self.fetch_basic_land_type = fetch_basic_land_type 
        
        # --- DEBUG PRINT IN INIT ---
        logger.debug(f"ScryfallCardProcessor __init__: input_file='{self.input_file}', fetch_basic_land_type='{self.fetch_basic_land_type}'")
        # --- END DEBUG ---

        self.frame_config = get_frame_config(frame_type)
        self.scryfall_api = ScryfallAPI()  
        self.color_detector = ColorDetector() 
        self.card_builder = CardBuilder(
            frame_type, 
            self.frame_config, 
            frame_set, 
            legendary_crowns, 
            auto_fit_art, 
            set_symbol_override, 
            auto_fit_set_symbol, 
            api_delay_seconds
        ) 
    
    def load_cards_from_file(self) -> List[str]:
        card_names = []
        if not self.input_file: 
            logger.error("load_cards_from_file called but no input file was provided to the processor.")
            return []
        try:
            with open(self.input_file, 'r') as file:
                for line in file:
                    processed_line = line.strip()
                    if re.match(r"^\d+", processed_line):
                        match = re.match(r"^\d+[xX\s]*(.+)", processed_line)
                        if match:
                            card_name = match.group(1).strip() 
                            if card_name: 
                                card_names.append(card_name)
            
            if not card_names:
                logger.warning(f"No valid card names (lines starting with digits) found in input file: {self.input_file}")
            else:
                logger.info(f"Successfully loaded {len(card_names)} card names from decklist file: {self.input_file}")
            return card_names
        except FileNotFoundError:
            logger.error(f"Input file not found: {self.input_file}")
            return [] 
        except Exception as e:
            logger.error(f"Error reading input file {self.input_file}: {e}")
            return []
    
    def process_cards(self) -> List[Dict]:
        """Process all cards from the input file OR fetch all printings of a basic land."""
        
        # --- DEBUG PRINT AT START OF process_cards ---
        logger.debug(f"ScryfallCardProcessor.process_cards: self.input_file='{self.input_file}', self.fetch_basic_land_type='{self.fetch_basic_land_type}'")
        # --- END DEBUG ---

        items_to_process = [] 

        if self.fetch_basic_land_type: # This mode takes precedence
            logger.info(f"Mode: Fetching all printings for basic land type: {self.fetch_basic_land_type}")
            all_printings = self.scryfall_api.get_all_printings_of_basic_land(self.fetch_basic_land_type)
            for printing_data in all_printings:
                generic_land_name = self.fetch_basic_land_type 
                set_code = printing_data.get("set", "UNKSET").lower()
                collector_num = printing_data.get("collector_number", "000")
                unique_card_key = f"{generic_land_name}-{set_code}-{collector_num}"
                items_to_process.append({
                    "key_name": unique_card_key, 
                    "card_data_obj": printing_data, 
                    "is_basic_land_fetch_item": True
                })
        elif self.input_file: 
            logger.info(f"Mode: Processing cards from input file: {self.input_file}")
            card_names_from_file = self.load_cards_from_file() 
            if not card_names_from_file:
                logger.warning(f"No cards loaded from file '{self.input_file}'.")
                return []
            for card_name in card_names_from_file:
                items_to_process.append({
                    "key_name": card_name, 
                    "name_to_fetch": card_name, 
                    "is_basic_land_fetch_item": False
                })
        else:
            logger.error("No input source specified for ScryfallCardProcessor (self.fetch_basic_land_type and self.input_file are both Falsy).")
            return []

        if not items_to_process: 
            logger.warning("No items to process after determining mode.")
            return []
            
        result = []
        num_items = len(items_to_process)
        
        for i, item_info in enumerate(items_to_process):
            card_key = item_info["key_name"] 
            is_basic_land_item = item_info["is_basic_land_fetch_item"]
            
            card_data_scryfall = None
            if is_basic_land_item:
                card_data_scryfall = item_info["card_data_obj"]
                logger.info(f"Processing basic land printing ({i+1}/{num_items}): {card_key} from set {card_data_scryfall.get('set')}")
                if self.api_delay_seconds > 0 and i < num_items - 1:
                    time.sleep(self.api_delay_seconds)
            else: 
                name_to_fetch = item_info["name_to_fetch"]
                logger.info(f"Processing card from file ({i+1}/{num_items}): {name_to_fetch}")
                card_data_scryfall = self.scryfall_api.get_earliest_printing(name_to_fetch)
                if card_data_scryfall and self.api_delay_seconds > 0: 
                    time.sleep(self.api_delay_seconds)

            if not card_data_scryfall: 
                logger.warning(f"No Scryfall data found for item '{card_key}', skipping.")
                if not is_basic_land_item and self.api_delay_seconds > 0 and i < num_items - 1: 
                     time.sleep(self.api_delay_seconds)
                continue 
            
            try:
                color_info = ColorDetector.get_color_info(card_data_scryfall) 
                card_object = self.card_builder.build_card_data(
                    card_key, 
                    card_data_scryfall, 
                    color_info,
                    is_basic_land_fetch_mode=is_basic_land_item,
                    basic_land_type_override=self.fetch_basic_land_type if is_basic_land_item else None
                )
                result.append(card_object)
            except Exception as e:
                logger.error(f"Unexpected error processing card data for '{card_key}': {e}", exc_info=True)
        
        return result
    
    def save_output(self, output_file: str, data: List[Dict]):
        try:
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            logger.info(f"Output saved to {output_file}")
        except Exception as e:
            logger.error(f"Error saving output file: {e}")
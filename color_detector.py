# --- file: color_detector.py ---
"""
Module for detecting card colors from Scryfall data
"""
import logging
from typing import Dict, List, Union

from color_mapping import COLOR_CODE_MAP

logger = logging.getLogger(__name__)

class ColorDetector:
    """Class for detecting card colors from Scryfall data"""
    
    @staticmethod
    def detect_dual_land_colors(card_data: Dict) -> List[Dict]:
        """
        Detect dual land colors by analyzing oracle text.
        Returns a list of color dictionaries in the order they appear in the text.
        """
        # First verify this is a land card
        if 'type_line' not in card_data or 'Land' not in card_data['type_line']:
            return []
            
        # Need oracle text to detect mana symbols
        if 'oracle_text' not in card_data or not card_data['oracle_text']:
            return []
            
        oracle_text = card_data['oracle_text']
        
        mana_positions = []
        # Iterate through WUBRG for mana symbols, explicitly exclude others
        for color_key in ['W', 'U', 'B', 'R', 'G']: 
            color_info = COLOR_CODE_MAP.get(color_key)
            if not color_info: continue # Should not happen with WUBRG

            mana_symbol = f"{{{color_key}}}" # e.g. {W}, {U}
            index = oracle_text.find(mana_symbol) 
            
            if index != -1: 
                mana_positions.append({
                    "position": index,
                    "color_key": color_key, 
                    "color_info": color_info 
                })

        if len(mana_positions) >= 2:
            mana_positions.sort(key=lambda x: x["position"])
            return [COLOR_CODE_MAP.get('L', {'code': 'l', 'name': 'Land'})] + [item["color_info"] for item in mana_positions]
        
        return []

    @staticmethod
    def get_color_info(card_data: Dict) -> Union[Dict, List[Dict]]:
        """Extract color information from card data.
        
        For dual lands and multicolor cards, may return a list of color info dictionaries or a complex dict.
        """
        card_name = card_data.get('name', 'Unknown Card')
        type_line = card_data.get('type_line', '')

        # --- Vehicle Check (Vehicles are also Artifacts, so check first) ---
        if 'Vehicle' in type_line:
            logger.debug(f"Card '{card_name}' is a Vehicle.")
            # Vehicles are usually colorless artifacts but can be colored.
            # For frame purposes, we might just use 'V' for its P/T box.
            # If it has Scryfall colors, it's a colored artifact vehicle.
            scryfall_colors = card_data.get('colors', [])
            if scryfall_colors: # Colored Vehicle
                if len(scryfall_colors) >= 2: # Multicolor Vehicle
                    component_colors = [COLOR_CODE_MAP[c] for c in scryfall_colors if c in COLOR_CODE_MAP]
                    return {
                        **(COLOR_CODE_MAP.get('V') or COLOR_CODE_MAP.get('M') or COLOR_CODE_MAP.get('A')), # Base on V, M, then A
                        'name': "Multicolored Vehicle",
                        'is_gold': True,
                        'is_artifact': True,
                        'is_vehicle': True,
                        'component_colors': component_colors
                    }
                elif len(scryfall_colors) == 1: # Monocolored Vehicle
                     color_key = scryfall_colors[0]
                     return {
                        **(COLOR_CODE_MAP.get(color_key) or COLOR_CODE_MAP.get('V')), # Base on its color, fallback to V
                        'name': f"{COLOR_CODE_MAP[color_key]['name']} Vehicle",
                        'is_artifact': True,
                        'is_vehicle': True,
                     }
            # Default (colorless) Vehicle
            return {**(COLOR_CODE_MAP.get('V') or COLOR_CODE_MAP.get('A')), 'is_vehicle': True, 'is_artifact': True}


        # --- Artifact Check ---
        if 'Artifact' in type_line: # Catches non-Vehicle artifacts
            logger.debug(f"Card '{card_name}' is an Artifact (non-vehicle).")
            scryfall_colors = card_data.get('colors', [])
            if scryfall_colors: # Colored Artifact
                if len(scryfall_colors) >= 2: # Multicolor Artifact
                    component_colors = [COLOR_CODE_MAP[c] for c in scryfall_colors if c in COLOR_CODE_MAP]
                    base_code = COLOR_CODE_MAP.get('MA', COLOR_CODE_MAP.get('M', COLOR_CODE_MAP.get('A')))
                    return {
                        **base_code,
                        'name': "Multicolored Artifact",
                        'is_gold': True, 
                        'is_artifact': True,
                        'component_colors': component_colors
                    }
                elif len(scryfall_colors) == 1: # Monocolored Artifact
                    color_key = scryfall_colors[0]
                    return {
                        **COLOR_CODE_MAP[color_key],
                        'name': f"{COLOR_CODE_MAP[color_key]['name']} Artifact",
                        'is_artifact': True,
                    }
            # Colorless non-vehicle Artifact
            return {**(COLOR_CODE_MAP.get('A') or COLOR_CODE_MAP.get('C')), 'is_artifact': True}


        # --- Land Check ---
        if 'Land' in type_line:
            logger.debug(f"Card '{card_name}' is a Land.")
            multi_land_colors = ColorDetector.detect_dual_land_colors(card_data)
            if multi_land_colors:
                logger.debug(f"Detected multicolor land colors for '{card_name}': {[c['name'] for c in multi_land_colors[1:]]}")
                return multi_land_colors
            
            type_line_lower = type_line.lower()
            basic_land_map = {'plains': 'W', 'island': 'U', 'swamp': 'B', 'mountain': 'R', 'forest': 'G'}
            for land_name_part, color_key in basic_land_map.items():
                if land_name_part in type_line_lower:
                    logger.debug(f"Detected basic land '{card_name}' as {COLOR_CODE_MAP[color_key]['name']}.")
                    return [COLOR_CODE_MAP.get('L', {'code':'l', 'name':'Land'}), COLOR_CODE_MAP[color_key]]
            
            logger.debug(f"'{card_name}' is a generic/other Land.")
            # Ensure 'L' is a list for consistency if other land types return lists
            base_land_info = COLOR_CODE_MAP.get('L', {'code':'l', 'name':'Land'})
            return [base_land_info] if isinstance(base_land_info, dict) else base_land_info
                

        # --- Other Card Types (Creatures, Enchantments, Sorceries, Instants) ---
        # Not an Artifact, not a Land, not a Vehicle
        scryfall_colors = card_data.get('colors') # Scryfall uses null for colorless, or empty list
        
        if scryfall_colors is None or not scryfall_colors: # Colorless (true colorless, e.g. Eldrazi)
            logger.debug(f"Card '{card_name}' is Colorless (non-artifact, non-land, non-vehicle).")
            return COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'}) # Use 'C' mapping
        
        num_colors = len(scryfall_colors)
        if num_colors == 1: # Monocolored
            color_key = scryfall_colors[0]
            if color_key in COLOR_CODE_MAP:
                logger.debug(f"Card '{card_name}' is Monocolored: {COLOR_CODE_MAP[color_key]['name']}.")
                return COLOR_CODE_MAP[color_key]
            else:
                logger.warning(f"Unknown monocolor key '{color_key}' for card '{card_name}'. Defaulting to Colorless.")
                return COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})
        
        elif num_colors >= 2: # Multicolor / Gold
            logger.debug(f"Card '{card_name}' is Multicolor/Gold. Colors: {scryfall_colors}.")
            component_color_dicts = [COLOR_CODE_MAP[c] for c in scryfall_colors if c in COLOR_CODE_MAP]
            
            if component_color_dicts:
                base_multicolor_info = COLOR_CODE_MAP.get('M', {'code': 'm', 'name': 'Multicolored'})
                return {
                    **base_multicolor_info,
                    'is_gold': True,
                    'component_colors': component_color_dicts
                }
            else:
                logger.warning(f"Could not map component colors for gold card '{card_name}'. Colors: {scryfall_colors}. Defaulting.")
                return COLOR_CODE_MAP.get('M', COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})) 
        
        logger.warning(f"No color explicitly found for '{card_name}'. Defaulting to Colorless.")
        return COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})
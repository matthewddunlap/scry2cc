# --- file: color_detector.py ---
"""
Module for detecting card colors from Scryfall data
"""
import logging
from typing import Dict, List, Union
import re # Import re for more complex parsing if needed, though not used in this version yet

from color_mapping import COLOR_CODE_MAP

logger = logging.getLogger(__name__)

class ColorDetector:
    """Class for detecting card colors from Scryfall data"""
    
    @staticmethod
    def detect_producing_land_colors(card_data: Dict) -> List[Dict]:
        """
        Detects colors a land can produce by analyzing oracle text.
        Returns a list: [BaseLandInfo, Color1Info, Color2Info, ...]
        If no specific WUBRG colors found, returns [BaseLandInfo].
        Returns empty list if not a land or no oracle text.
        """
        if 'type_line' not in card_data or 'Land' not in card_data['type_line']:
            return [] # Not a land
            
        # For lands like Strip Mine or Wastes with no WUBRG mana abilities in oracle text,
        # or if oracle_text is missing (though unlikely for functional lands).
        if 'oracle_text' not in card_data or not card_data['oracle_text']:
            return [COLOR_CODE_MAP.get('L', {'code': 'l', 'name': 'Land'})] 
            
        oracle_text = card_data['oracle_text']
        
        mana_positions = []
        # Iterate through WUBRG for mana symbols
        for color_key_scryfall in ['W', 'U', 'B', 'R', 'G']: # Scryfall keys W, U, B, R, G
            internal_color_info = COLOR_CODE_MAP.get(color_key_scryfall) 
            if not internal_color_info: continue

            mana_symbol_scryfall = f"{{{color_key_scryfall}}}" # e.g. {W}, {U}
            
            # Simple heuristic: check if "Add {COLOR}" exists.
            # This might need to be more robust for complex mana abilities.
            # For example, "Add one mana of any color" or conditional additions.
            # Using regex for "Add {X}" or "{T}: Add {X}" patterns would be more robust.
            # For now, a simple find of the mana symbol within an "Add" context.
            
            # A common pattern is "{T}: Add {U}." or "Add {U}."
            # We are looking for mana symbols that are *produced*.
            
            # Find all occurrences of "Add {COLOR}" pattern.
            # Example: "Add {U}", "Add {G}{G}", or part of "Add {W} or {U}"
            # A simple find for "{COLOR}" is okay if we assume any listed symbol in oracle text for a land is producible.
            # More complex parsing would be needed to be 100% accurate for all edge cases.
            
            # Let's look for "{T}: Add {SYMBOL}" or "Add {SYMBOL}" to be more specific
            # This regex looks for the mana symbol if it's preceded by "Add " or ": Add "
            # It's a simplified check.
            #pattern = r"(?:Add\s|\:\s*Add\s)" + re.escape(mana_symbol_scryfall)
            #if re.search(pattern, oracle_text, re.IGNORECASE):
            # Look for mana symbols that appear after "Add" in the oracle text
            # This handles cases like "Add {U}{R}" where both symbols should be detected
            # First, find all "Add" clauses (everything from "Add" to the next period or semicolon)
            add_clauses = re.findall(r'Add\s[^.;]+', oracle_text, re.IGNORECASE)
            
            # Check if the mana symbol appears in any "Add" clause
            found_in_add_clause = False
            for clause in add_clauses:
                if mana_symbol_scryfall in clause:
                    found_in_add_clause = True
                    break
            
            if found_in_add_clause:
                # Ensure we only add each color once, even if mentioned multiple times
                if not any(item['color_key'] == color_key_scryfall for item in mana_positions):
                    # Find the first actual occurrence to sort by position if needed,
                    # though order might not be super critical if we just list producing colors.
                    first_occurrence_index = oracle_text.find(mana_symbol_scryfall)
                    mana_positions.append({
                        "position": first_occurrence_index if first_occurrence_index != -1 else float('inf'),
                        "color_key": color_key_scryfall, 
                        "color_info": internal_color_info 
                    })

        # Sort by the order they appear in text (if multiple distinct colors are found)
        mana_positions.sort(key=lambda x: x["position"])
        
        base_land = COLOR_CODE_MAP.get('L', {'code': 'l', 'name': 'Land'})
        if mana_positions: # If any WUBRG producing abilities were found
            return [base_land] + [item["color_info"] for item in mana_positions]
        else:
            # If no WUBRG symbols found in "Add" abilities (e.g. Strip Mine "Add {C}")
            return [base_land]

    @staticmethod
    def get_color_info(card_data: Dict) -> Union[Dict, List[Dict]]:
        """Extract color information from card data."""
        card_name = card_data.get('name', 'Unknown Card')
        type_line = card_data.get('type_line', '')

        if 'Vehicle' in type_line:
            # logger.debug(f"Card '{card_name}' is a Vehicle.") # Debugging removed
            scryfall_colors = card_data.get('colors', [])
            if scryfall_colors: 
                if len(scryfall_colors) >= 2: 
                    component_colors = [COLOR_CODE_MAP[c] for c in scryfall_colors if c in COLOR_CODE_MAP]
                    return {**(COLOR_CODE_MAP.get('V') or COLOR_CODE_MAP.get('M') or COLOR_CODE_MAP.get('A')), 'name': "Multicolored Vehicle", 'is_gold': True, 'is_artifact': True, 'is_vehicle': True, 'component_colors': component_colors }
                elif len(scryfall_colors) == 1: 
                     color_key = scryfall_colors[0]
                     return {**(COLOR_CODE_MAP.get(color_key) or COLOR_CODE_MAP.get('V')), 'name': f"{COLOR_CODE_MAP.get(color_key,{}).get('name','Unknown Color')} Vehicle", 'is_artifact': True, 'is_vehicle': True }
            return {**(COLOR_CODE_MAP.get('V') or COLOR_CODE_MAP.get('A')), 'is_vehicle': True, 'is_artifact': True}

        if 'Artifact' in type_line: 
            # logger.debug(f"Card '{card_name}' is an Artifact (non-vehicle).") # Debugging removed
            scryfall_colors = card_data.get('colors', [])
            if scryfall_colors: 
                if len(scryfall_colors) >= 2: 
                    component_colors = [COLOR_CODE_MAP[c] for c in scryfall_colors if c in COLOR_CODE_MAP]
                    base_code = COLOR_CODE_MAP.get('MA', COLOR_CODE_MAP.get('M', COLOR_CODE_MAP.get('A')))
                    return {**base_code, 'name': "Multicolored Artifact", 'is_gold': True, 'is_artifact': True, 'component_colors': component_colors}
                elif len(scryfall_colors) == 1: 
                    color_key = scryfall_colors[0]
                    return {**COLOR_CODE_MAP[color_key], 'name': f"{COLOR_CODE_MAP.get(color_key,{}).get('name','Unknown Color')} Artifact", 'is_artifact': True }
            return {**(COLOR_CODE_MAP.get('A') or COLOR_CODE_MAP.get('C')), 'is_artifact': True}

        if 'Land' in type_line:
            # logger.debug(f"Card '{card_name}' is a Land.") # Debugging removed
            producing_land_colors = ColorDetector.detect_producing_land_colors(card_data) # Use new method
            
            if len(producing_land_colors) > 1: # It produces specific WUBRG colors
                # logger.debug(f"Detected producing land colors for '{card_name}': {[c['name'] for c in producing_land_colors[1:]]}") # Debug
                return producing_land_colors
            
            type_line_lower = type_line.lower()
            basic_land_map = {'plains': 'W', 'island': 'U', 'swamp': 'B', 'mountain': 'R', 'forest': 'G'}
            for land_name_part, color_key in basic_land_map.items():
                if land_name_part in type_line_lower:
                    # logger.debug(f"Detected basic land '{card_name}' as {COLOR_CODE_MAP[color_key]['name']}.") # Debug
                    return [COLOR_CODE_MAP.get('L', {'code':'l', 'name':'Land'}), COLOR_CODE_MAP[color_key]]
            
            # If not a WUBRG-producing land from oracle text, and not a named basic land,
            # it's a generic land (like Wastes, or Strip Mine if its {C} wasn't parsed as a WUBRG color).
            # detect_producing_land_colors should have returned [BaseLandInfo] for these.
            # logger.debug(f"'{card_name}' is a generic land (returned from detect_producing_land_colors or as fallback).") # Debug
            return producing_land_colors if producing_land_colors else [COLOR_CODE_MAP.get('L', {'code':'l', 'name':'Land'})]
                
        scryfall_colors = card_data.get('colors')
        if scryfall_colors is None or not scryfall_colors: 
            # logger.debug(f"Card '{card_name}' is Colorless (non-artifact, non-land, non-vehicle).") # Debug
            return COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})
        
        num_colors = len(scryfall_colors)
        if num_colors == 1: 
            color_key = scryfall_colors[0]
            if color_key in COLOR_CODE_MAP: 
                # logger.debug(f"Card '{card_name}' is Monocolored: {COLOR_CODE_MAP[color_key]['name']}.") # Debug
                return COLOR_CODE_MAP[color_key]
            else: 
                # logger.warning(f"Unknown monocolor key '{color_key}' for card '{card_name}'. Defaulting to Colorless.") # Debug
                return COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})
        elif num_colors >= 2: 
            # logger.debug(f"Card '{card_name}' is Multicolor/Gold. Colors: {scryfall_colors}.") # Debug
            component_color_dicts = [COLOR_CODE_MAP[c] for c in scryfall_colors if c in COLOR_CODE_MAP]
            if component_color_dicts:
                base_multicolor_info = COLOR_CODE_MAP.get('M', {'code': 'm', 'name': 'Multicolored'})
                return {**base_multicolor_info, 'is_gold': True, 'component_colors': component_color_dicts}
            else: 
                # logger.warning(f"Could not map component colors for gold card '{card_name}'. Colors: {scryfall_colors}. Defaulting.") # Debug
                return COLOR_CODE_MAP.get('M', COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})) 
        
        # logger.warning(f"No color explicitly found for '{card_name}'. Defaulting to Colorless.") # Debug
        return COLOR_CODE_MAP.get('C', {'code': 'c', 'name': 'Colorless'})

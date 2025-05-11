"""
Color and rarity mapping for MTG cards
"""

# Color mapping
COLOR_CODE_MAP = {
    'W': {'code': 'w', 'name': 'White'},
    'U': {'code': 'u', 'name': 'Blue'},
    'B': {'code': 'b', 'name': 'Black'},
    'R': {'code': 'r', 'name': 'Red'},
    'G': {'code': 'g', 'name': 'Green'},
    # For lands and colorless cards
    'C': {'code': 'c', 'name': 'Colorless'},    # For true colorless cards (non-artifact)
    'L': {'code': 'l', 'name': 'Land'},         # For lands
    'A': {'code': 'a', 'name': 'Artifact'},     # For Artifacts (used by M15 regular, can be base for colored artifacts)
    'M': {'code': 'm', 'name': 'Multicolored'}, # For M15 Gold/Multicolor frames
    'V': {'code': 'v', 'name': 'Vehicle'}       # For Vehicle P/T boxes on m15ub
}

# Rarity mapping
RARITY_MAP = {
    'common': 'c',
    'uncommon': 'u',
    'rare': 'r',
    'mythic': 'm',
    'special': 's',
    'bonus': 'b'
}
# scry2Cc
Generate Card Conjurer import file from Scryfall

## Summary
(https://github.com/matthewddunlap/scry2cc) takes a deck list as input and queries [Scryfall](https://scryfall.com/) via API to fetch card data then formats it in JSON as a `.cardconjurer` file for automated processing via [ccDownloader](https://github.com/matthewddunlap/ccDownloader).

## Requirements
On Debian 12 install requried package
```
sudo apt install chromium chromium-driver jq python3-lxml python3-natsort python3-pil python3-reportlab python3-requests python3-selenium
```

## Examples
### Create Card Conjurer save file for import from saved deck list
This is the most common use case of preparing a deck list for download with [ccDownloader](https://github.com/matthewddunlap/ccDownloader) followed by printing with [MtgPng2Pdf](https://github.com/matthewddunlap/MtgPng2Pdf).
```
python3 scry2cc.py --frame m15ub --output_file myDeck.cardcojurer myDeck.txt
```

### Create Card Conjurer save file for import of all unique art prints of forest
This is an optional one time operation. By default [MtgPng2Pdf](https://github.com/matthewddunlap/MtgPng2Pdf) will use a random selection of land art.
```
python3 scry2cc.py --frame m15ub --fetch_basic_land Forest --output_file forest.cardconjurer
```


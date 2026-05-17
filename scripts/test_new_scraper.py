import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.scraper_tool import ScraperTool
import json

s = ScraperTool()
result = s._run("upwork")
print(result[:4000])

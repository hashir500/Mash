import re

output = """
         dict entry(
            string "xesam:title"
            variant                string "FRANCHISE (feat. Future, Young Thug & M.I.A.) - REMIX"
         )
"""

title_m = re.search(r'xesam:title"\s+variant\s+string\s+"(.*?)"', output, re.DOTALL)
if title_m:
    print(f"FOUND: {title_m.group(1)}")
else:
    print("NOT FOUND")

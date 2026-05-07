from PIL import Image
import numpy as np

try:
    img = Image.open('icon.png').convert('L') # grayscale
    img = img.resize((32, 16)) # 32x16 to account for terminal character aspect ratio
    pixels = np.array(img)
    chars = " .:-=+*#%@"
    for row in pixels:
        line = "".join([chars[int(p / 256 * len(chars))] for p in row])
        print(line)
except Exception as e:
    print(e)

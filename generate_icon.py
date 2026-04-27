from PIL import Image
img = Image.open("family_tree.png")
icon_sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
img.save("family_tree.ico", sizes=icon_sizes)
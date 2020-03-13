import time
import psutil
from src import pytesseract
import os
try:
    from PIL import Image
except ImportError:
    import Image

def to_pdf(images, remove=True,fname_out="1"):
    images[0].save(fname_out, format='tiff', append_images=images[1:], save_all=True, duration=500, loop=0)
    pdf = pytesseract.image_to_pdf_or_hocr(fname_out[:-4] + '.tiff')
    if remove:
        os.remove(fname_out)
    with open(fname_out[:-4] + '.pdf', 'w+b') as f:
        f.write(bytearray(pdf))

def ensure_directory():
    if not os.path.exists(temppath):
        os.mkdir(temppath)

if __name__ == '__main__':
    number_images = 1000
    temppath = 'temp_pdf_path'
    images = []
    print("current memory usage pre image loading : ")
    print(psutil.virtual_memory())
    for _ in range(number_images):
        images.append(Image.open('tests/data/test.png'))
    print("current memory usage after image loading : ")
    print(psutil.virtual_memory())
    time.sleep(5)
    print("current memory usage after waiting : ")
    print(psutil.virtual_memory())

    for i,im in enumerate(images):
        print("current memory usage before image {} : ".format(i))
        print(psutil.virtual_memory())
        to_pdf(temppath, i)
    print("current memory usage after all images written : ")
    print(psutil.virtual_memory())
    time.sleep(15)
    print("current memory usage after waiting : ")
    print(psutil.virtual_memory())








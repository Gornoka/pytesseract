import sys
import time
import tracemalloc
import copy
import psutil
import shutil
from src import pytesseract
import os
from PIL import Image

def to_pdf(images, remove=True, fname_out="1"):
    images[0].save(fname_out, format='tiff', append_images=images[1:], save_all=True, duration=500, loop=0,lang='eng+ger')
    pdf = pytesseract.image_to_pdf_or_hocr(fname_out[:-5] + '.tiff')
    if remove:
        os.remove(fname_out)
    with open(fname_out[:-5] + '.pdf', 'w+b') as f:
        f.write(bytearray(pdf))


def to_pdf2(images, remove=True, fname_out="1"):
    images[0].save(fname_out, format='tiff', append_images=images[1:], save_all=True, duration=500, loop=0)
    if remove:
        os.remove(fname_out)


class Image_class:
    def __init__(self, images=[]):
        self.images = images

    def to_pdf2(self, remove=True, fname_out="1"):
        self.images[0].save(fname_out, format='tiff', append_images=self.images[1:], save_all=True, duration=500,
                            loop=0)
        if remove:
            os.remove(fname_out)

    def read_images(self, _number_images):
        for _ in range(_number_images):
            self.images.append(Image.open('tests/data/test.tiff'))

    def destroy_images(self):
        self.images = []


def ensure_directory(path):
    if not os.path.exists(path):
        os.mkdir(path)


if __name__ == '__main__':

    tracemalloc.start()
    number_images = 20
    number_repetitions = 10
    temppath = 'temp_pdf_path'
    images = []

    print("current memory usage pre image loading : ")
    print(tracemalloc.get_traced_memory())
    for _ in range(number_images):
        images.append(Image.open('tests/data/test.tiff'))
    image_classes = [Image_class() for _ in range(number_repetitions)]
    print("current memory usage after image loading : ")
    print(tracemalloc.get_traced_memory())
    print("current memory usage after waiting : ")
    print(tracemalloc.get_traced_memory())
    ensure_directory(temppath)
    print("waiting for tesseract instantiation")
    time.sleep(15)
    last_time = time.time()
    for i in range(number_repetitions):
        print("current memory usage before image {}, size of images {} : ".format(i, sys.getsizeof(images)))
        print(tracemalloc.get_traced_memory())
        """
        image_classes[i].read_images(number_images)
        image_classes[i].to_pdf2()
        image_classes[i].destroy_images()
        """
        to_pdf(images, fname_out=temppath + '/' + str(i) + ".tiff")
        print('time for iteration: ', time.time()-last_time)
        last_time=time.time()
    print("current memory usage after all images written : ")
    print(tracemalloc.get_traced_memory())
    time.sleep(15)
    print("current memory usage after waiting : ")
    print(tracemalloc.get_traced_memory())
    print('deleting temporary files')
    shutil.rmtree(temppath)

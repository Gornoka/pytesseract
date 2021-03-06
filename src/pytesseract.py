#!/usr/bin/env python

import shlex
import string
import subprocess
import sys
from contextlib import contextmanager
from csv import QUOTE_NONE
from distutils.version import LooseVersion
from errno import ENOENT
from functools import wraps
from glob import iglob
from io import BytesIO
from os import environ, extsep, remove
from os.path import normcase, normpath, realpath
from pkgutil import find_loader
from tempfile import NamedTemporaryFile
from threading import Timer

try:
    from PIL import Image
except ImportError:
    import Image

tesseract_cmd = 'tesseract'

numpy_installed = find_loader('numpy') is not None
if numpy_installed:
    from numpy import ndarray

pandas_installed = find_loader('pandas') is not None
if pandas_installed:
    import pandas as pd

RGB_MODE = 'RGB'
SUPPORTED_FORMATS = {
    'JPEG',
    'PNG',
    'PBM',
    'PGM',
    'PPM',
    'TIFF',
    'BMP',
    'GIF',
    'WEBP',
}

OSD_KEYS = {
    'Page number': ('page_num', int),
    'Orientation in degrees': ('orientation', int),
    'Rotate': ('rotate', int),
    'Orientation confidence': ('orientation_conf', float),
    'Script': ('script', str),
    'Script confidence': ('script_conf', float),
}


class Output:
    BYTES = 'bytes'
    DATAFRAME = 'data.frame'
    DICT = 'dict'
    STRING = 'string'
    OBJECT = 'object'


class PandasNotSupported(EnvironmentError):
    def __init__(self):
        super(PandasNotSupported, self).__init__('Missing pandas package')


class TesseractError(RuntimeError):
    def __init__(self, status, message):
        self.status = status
        self.message = message
        self.args = (status, message)


class TesseractNotFoundError(EnvironmentError):
    def __init__(self):
        super(TesseractNotFoundError, self).__init__(
            tesseract_cmd + " is not installed or it's not in your PATH",
        )


class TSVNotSupported(EnvironmentError):
    def __init__(self):
        super(TSVNotSupported, self).__init__(
            'TSV output not supported. Tesseract >= 3.05 required',
        )


class DataLine:
    def __init__(self, data_string, headers):
        """
        Presents data from image as object.
        :param data_string: str
        :param headers: str
        """
        """
        The following attributes are expected to be available with tesseract
        version 5, 4 and 3.05+, this may change with future versions of
        tesseract.
        Regardless of this the returned object has all attributes found in
        the header string.
        This also assists IDE's in autodecting available parameters and types.
        Default values ensure compatibility with python < 3.6.
        """
        self.default_int = -2
        self.default_str = '\t'

        self.level = self.default_int
        self.page_num = self.default_int
        self.block_num = self.default_int
        self.par_num = self.default_int
        self.line_num = self.default_int
        self.word_num = self.default_int
        self.left = self.default_int
        self.top = self.default_int
        self.width = self.default_int
        self.height = self.default_int
        self.conf = self.default_int
        self.text = self.default_str

        self.__fill_from_string(data_string, headers)

    def __fill_from_string(self, data_string, headers):
        """

        :param data_string:str
        :param headers: str
        :return: None
        """
        data_list = data_string.split('\t')
        for i in range(len(headers)):
            try:
                setattr(self, headers[i], int(data_list[i]))
            except ValueError:
                setattr(self, headers[i], data_list[i])

    def __str__(self):
        slist = []
        for key in self.__dict__.keys():
            slist.append(str(self.__dict__[key]))
        return '\t'.join(slist)


class Data:
    def __init__(self, data_str):
        """
        Python object representation of tesseract data as received from
        pytesseract.imagetodata().
        Contains all lines found in the data_str as DataLine object.
        :param data_str: str
        """
        self.lines = []
        data_list = data_str.split('\n')
        self.header_str = data_list[0]
        headers = self.header_str.split('\t')
        for line in data_list[1:]:
            self.lines.append(DataLine(line, headers))

    def __iter__(self):
        return self.lines.__iter__()

    def __str__(self):
        slist = [self.header_str]
        for line in self.lines:
            slist.append(str(line))
        return '\n'.join(slist)


def kill(process, code):
    process.kill()
    process.returncode = code


@contextmanager
def timeout_manager(proc, seconds=0):
    try:
        if not seconds:
            yield proc.communicate()[1]
            return

        timeout_code = -1
        timer = Timer(seconds, kill, [proc, timeout_code])
        timer.start()
        try:
            _, error_string = proc.communicate()
            yield error_string
        finally:
            timer.cancel()
            if proc.returncode == timeout_code:
                raise RuntimeError('Tesseract process timeout')
    finally:
        proc.stdin.close()
        proc.stdout.close()
        proc.stderr.close()


def run_once(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if wrapper._result is wrapper:
            wrapper._result = func(*args, **kwargs)
        return wrapper._result

    wrapper._result = wrapper
    return wrapper


def get_errors(error_string):
    return u' '.join(
        line for line in error_string.decode('utf-8').splitlines()
    ).strip()


def cleanup(temp_name):
    """ Tries to remove temp files by filename wildcard path. """
    for filename in iglob(temp_name + '*' if temp_name else temp_name):
        try:
            remove(filename)
        except OSError as e:
            if e.errno != ENOENT:
                raise e


def prepare(image):
    if numpy_installed and isinstance(image, ndarray):
        image = Image.fromarray(image)

    if not isinstance(image, Image.Image):
        raise TypeError('Unsupported image object')

    extension = 'PNG' if not image.format else image.format
    if extension not in SUPPORTED_FORMATS:
        raise TypeError('Unsupported image format/type')

    if not image.mode.startswith(RGB_MODE):
        image = image.convert(RGB_MODE)

    if 'A' in image.getbands():
        # discard and replace the alpha channel with white background
        background = Image.new(RGB_MODE, image.size, (255, 255, 255))
        background.paste(image, (0, 0), image)
        image = background

    image.format = extension
    if 'format' not in image.info:
        image.info['format'] = extension

    return image, extension


@contextmanager
def save(image):
    try:
        with NamedTemporaryFile(prefix='tess_', delete=False) as f:
            if isinstance(image, str):
                yield f.name, realpath(normpath(normcase(image)))
                return

            image, extension = prepare(image)
            input_file_name = f.name + extsep + extension
            image.save(input_file_name, **image.info)
            yield f.name, input_file_name
    finally:
        cleanup(f.name)


def subprocess_args(include_stdout=True):
    # See https://github.com/pyinstaller/pyinstaller/wiki/Recipe-subprocess
    # for reference and comments.

    kwargs = {
        'stdin': subprocess.PIPE,
        'stderr': subprocess.PIPE,
        'startupinfo': None,
        'env': environ,
    }

    if hasattr(subprocess, 'STARTUPINFO'):
        kwargs['startupinfo'] = subprocess.STARTUPINFO()
        kwargs['startupinfo'].dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs['startupinfo'].wShowWindow = subprocess.SW_HIDE

    if include_stdout:
        kwargs['stdout'] = subprocess.PIPE

    return kwargs


def run_tesseract(
    input_filename,
    output_filename_base,
    extension,
    lang,
    config='',
    nice=0,
    timeout=0,
):
    cmd_args = []

    if not sys.platform.startswith('win32') and nice != 0:
        cmd_args += ('nice', '-n', str(nice))

    cmd_args += (tesseract_cmd, input_filename, output_filename_base)

    if lang is not None:
        cmd_args += ('-l', lang)

    if config:
        cmd_args += shlex.split(config)

    if extension and extension not in {'box', 'osd', 'tsv'}:
        cmd_args.append(extension)

    try:
        proc = subprocess.Popen(cmd_args, **subprocess_args())
    except OSError as e:
        if e.errno != ENOENT:
            raise e
        raise TesseractNotFoundError()

    with timeout_manager(proc, timeout) as error_string:
        if proc.returncode:
            raise TesseractError(proc.returncode, get_errors(error_string))


def run_and_get_output(
    image,
    extension='',
    lang=None,
    config='',
    nice=0,
    timeout=0,
    return_bytes=False,
):
    with save(image) as (temp_name, input_filename):
        kwargs = {
            'input_filename': input_filename,
            'output_filename_base': temp_name,
            'extension': extension,
            'lang': lang,
            'config': config,
            'nice': nice,
            'timeout': timeout,
        }

        run_tesseract(**kwargs)
        filename = kwargs['output_filename_base'] + extsep + extension
        with open(filename, 'rb') as output_file:
            if return_bytes:
                return output_file.read()
            return output_file.read().decode('utf-8').strip()


def file_to_dict(tsv, cell_delimiter, str_col_idx):
    result = {}
    rows = [row.split(cell_delimiter) for row in tsv.split('\n')]
    if not rows:
        return result

    header = rows.pop(0)
    length = len(header)
    if len(rows[-1]) < length:
        # Fixes bug that occurs when last text string in TSV is null, and
        # last row is missing a final cell in TSV file
        rows[-1].append('')

    if str_col_idx < 0:
        str_col_idx += length

    for i, head in enumerate(header):
        result[head] = list()
        for row in rows:
            if len(row) <= i:
                continue

            val = row[i]
            if row[i].isdigit() and i != str_col_idx:
                val = int(row[i])
            result[head].append(val)

    return result


def is_valid(val, _type):
    if _type is int:
        return val.isdigit()

    if _type is float:
        try:
            float(val)
            return True
        except ValueError:
            return False

    return True


def osd_to_dict(osd):
    return {
        OSD_KEYS[kv[0]][0]: OSD_KEYS[kv[0]][1](kv[1])
        for kv in (line.split(': ') for line in osd.split('\n'))
        if len(kv) == 2 and is_valid(kv[1], OSD_KEYS[kv[0]][1])
    }


@run_once
def get_tesseract_version():
    """
    Returns LooseVersion object of the Tesseract version
    """
    try:
        return LooseVersion(
            subprocess.check_output(
                [tesseract_cmd, '--version'], stderr=subprocess.STDOUT,
            )
            .decode('utf-8')
            .split()[1]
            .lstrip(string.printable[10:]),
        )
    except OSError:
        raise TesseractNotFoundError()


def image_to_string(
    image, lang=None, config='', nice=0, output_type=Output.STRING, timeout=0,
):
    """
    Returns the result of a Tesseract OCR run on the provided image to string
    """
    args = [image, 'txt', lang, config, nice, timeout]

    return {
        Output.BYTES: lambda: run_and_get_output(*(args + [True])),
        Output.DICT: lambda: {'text': run_and_get_output(*args)},
        Output.STRING: lambda: run_and_get_output(*args),
    }[output_type]()


def image_to_pdf_or_hocr(
    image, lang=None, config='', nice=0, extension='pdf', timeout=0,
):
    """
    Returns the result of a Tesseract OCR run on the provided image to pdf/hocr
    """

    if extension not in {'pdf', 'hocr'}:
        raise ValueError('Unsupported extension: {}'.format(extension))
    args = [image, extension, lang, config, nice, timeout, True]

    return run_and_get_output(*args)


def image_to_boxes(
    image, lang=None, config='', nice=0, output_type=Output.STRING, timeout=0,
):
    """
    Returns string containing recognized characters and their box boundaries
    """
    config += ' batch.nochop makebox'
    args = [image, 'box', lang, config, nice, timeout]

    return {
        Output.BYTES: lambda: run_and_get_output(*(args + [True])),
        Output.DICT: lambda: file_to_dict(
            'char left bottom right top page\n' + run_and_get_output(*args),
            ' ',
            0,
        ),
        Output.STRING: lambda: run_and_get_output(*args),
    }[output_type]()


def get_pandas_output(args, config=None):
    if not pandas_installed:
        raise PandasNotSupported()

    kwargs = {'quoting': QUOTE_NONE, 'sep': '\t'}
    try:
        kwargs.update(config)
    except (TypeError, ValueError):
        pass

    return pd.read_csv(BytesIO(run_and_get_output(*args)), **kwargs)


def image_to_data(
    image,
    lang=None,
    config='',
    nice=0,
    output_type=Output.STRING,
    timeout=0,
    pandas_config=None,
):
    """
    Returns string containing box boundaries, confidences,
    and other information. Requires Tesseract 3.05+
    """

    if get_tesseract_version() < '3.05':
        raise TSVNotSupported()

    config = '{} {}'.format('-c tessedit_create_tsv=1', config.strip()).strip()
    args = [image, 'tsv', lang, config, nice, timeout]

    return {
        Output.BYTES: lambda: run_and_get_output(*(args + [True])),
        Output.DATAFRAME: lambda: get_pandas_output(
            args + [True], pandas_config,
        ),
        Output.DICT: lambda: file_to_dict(run_and_get_output(*args), '\t', -1),
        Output.STRING: lambda: run_and_get_output(*args),
        Output.OBJECT: lambda: Data(run_and_get_output(*args)),
    }[output_type]()


def image_to_osd(
    image, lang='osd', config='', nice=0, output_type=Output.STRING, timeout=0,
):
    """
    Returns string containing the orientation and script detection (OSD)
    """
    config = '{}-psm 0 {}'.format(
        '' if get_tesseract_version() < '3.05' else '-', config.strip(),
    ).strip()
    args = [image, 'osd', lang, config, nice, timeout]

    return {
        Output.BYTES: lambda: run_and_get_output(*(args + [True])),
        Output.DICT: lambda: osd_to_dict(run_and_get_output(*args)),
        Output.STRING: lambda: run_and_get_output(*args),
    }[output_type]()


def main():
    if len(sys.argv) == 2:
        filename, lang = sys.argv[1], None
    elif len(sys.argv) == 4 and sys.argv[1] == '-l':
        filename, lang = sys.argv[3], sys.argv[2]
    else:
        sys.stderr.write('Usage: pytesseract [-l lang] input_file\n')
        exit(2)

    try:
        with Image.open(filename) as img:
            print(image_to_string(img, lang=lang))
    except TesseractNotFoundError as e:
        sys.stderr.write('{}\n'.format(str(e)))
        exit(1)
    except IOError:
        sys.stderr.write('ERROR: Could not open file "%s"\n' % filename)
        exit(1)


if __name__ == '__main__':
    main()

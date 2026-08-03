# Copyright (C) 2026 Sugar Labs
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Privacy-safe, in-memory reference image loading for the studio."""

from dataclasses import dataclass
import hashlib
import os

import gi
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import GdkPixbuf


MAX_SOURCE_BYTES = 8 * 1024 * 1024
MAX_NORMALIZED_BYTES = 4 * 1024 * 1024
MAX_DIMENSION = 10000
MAX_PIXELS = 40 * 1000 * 1000
NORMALIZED_EDGE = 1600
ALLOWED_FORMATS = ('png', 'jpeg')


class ReferenceImageError(ValueError):
    pass


@dataclass(frozen=True)
class ReferenceImage:
    data: bytes
    mime_type: str
    width: int
    height: int
    source_name: str
    sha256: str

    @property
    def byte_size(self):
        return len(self.data)


def normalize_reference_image(filename):
    """Decode, resize, strip metadata, and return an image held in memory."""
    try:
        source_size = os.path.getsize(filename)
    except OSError as error:
        raise ReferenceImageError('Could not read that image.') from error
    if source_size <= 0:
        raise ReferenceImageError('That image is empty.')
    if source_size > MAX_SOURCE_BYTES:
        raise ReferenceImageError('Choose an image smaller than 8 MB.')

    try:
        image_format, width, height = GdkPixbuf.Pixbuf.get_file_info(filename)
    except Exception as error:
        raise ReferenceImageError('That file is not a readable image.') \
            from error
    format_name = image_format.get_name() if image_format is not None else ''
    if format_name not in ALLOWED_FORMATS:
        raise ReferenceImageError('Choose a PNG or JPEG image.')
    if width <= 0 or height <= 0 or width > MAX_DIMENSION or \
            height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ReferenceImageError('That image is too large to process safely.')

    try:
        scale = min(1.0, float(NORMALIZED_EDGE) / max(width, height))
        target_width = max(1, int(round(width * scale)))
        target_height = max(1, int(round(height * scale)))
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
            filename, target_width, target_height, True)
        oriented = pixbuf.apply_embedded_orientation()
        if oriented is not None:
            pixbuf = oriented
        output_format = 'jpeg' if format_name == 'jpeg' else 'png'
        if output_format == 'jpeg':
            saved, data = pixbuf.save_to_bufferv(
                'jpeg', ['quality'], ['88'])
            mime_type = 'image/jpeg'
        else:
            saved, data = pixbuf.save_to_bufferv('png', [], [])
            mime_type = 'image/png'
        if saved and len(data) > MAX_NORMALIZED_BYTES and \
                output_format == 'png' and not pixbuf.get_has_alpha():
            output_format = 'jpeg'
            mime_type = 'image/jpeg'
            saved, data = pixbuf.save_to_bufferv(
                'jpeg', ['quality'], ['88'])
        while saved and len(data) > MAX_NORMALIZED_BYTES and \
                max(pixbuf.get_width(), pixbuf.get_height()) > 640:
            smaller = pixbuf.scale_simple(
                max(1, int(pixbuf.get_width() * 0.8)),
                max(1, int(pixbuf.get_height() * 0.8)),
                GdkPixbuf.InterpType.BILINEAR,
            )
            if smaller is None:
                break
            pixbuf = smaller
            if output_format == 'jpeg':
                saved, data = pixbuf.save_to_bufferv(
                    'jpeg', ['quality'], ['88'])
            else:
                saved, data = pixbuf.save_to_bufferv('png', [], [])
    except Exception as error:
        raise ReferenceImageError('Could not prepare that image.') from error
    if not saved or not data:
        raise ReferenceImageError('Could not prepare that image.')
    data = bytes(data)
    if len(data) > MAX_NORMALIZED_BYTES:
        raise ReferenceImageError(
            'The prepared image is still too large; choose a simpler image.')
    return ReferenceImage(
        data=data,
        mime_type=mime_type,
        width=pixbuf.get_width(),
        height=pixbuf.get_height(),
        source_name=os.path.basename(filename),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def normalize_reference_pixbuf(pixbuf, source_name='pasted-reference.png'):
    """Normalize a clipboard pixbuf without writing it to the filesystem."""
    try:
        width = pixbuf.get_width()
        height = pixbuf.get_height()
    except Exception as error:
        raise ReferenceImageError(
            'The clipboard does not contain a readable image.') from error
    if width <= 0 or height <= 0 or width > MAX_DIMENSION or \
            height > MAX_DIMENSION or width * height > MAX_PIXELS:
        raise ReferenceImageError(
            'The pasted image is too large to process safely.')

    try:
        scale = min(1.0, float(NORMALIZED_EDGE) / max(width, height))
        if scale < 1.0:
            pixbuf = pixbuf.scale_simple(
                max(1, int(round(width * scale))),
                max(1, int(round(height * scale))),
                GdkPixbuf.InterpType.BILINEAR,
            )
        if pixbuf is None:
            raise ReferenceImageError('Could not prepare the pasted image.')
        output_format = 'png'
        mime_type = 'image/png'
        saved, data = pixbuf.save_to_bufferv('png', [], [])
        if saved and len(data) > MAX_NORMALIZED_BYTES and \
                not pixbuf.get_has_alpha():
            output_format = 'jpeg'
            mime_type = 'image/jpeg'
            saved, data = pixbuf.save_to_bufferv(
                'jpeg', ['quality'], ['88'])
        while saved and len(data) > MAX_NORMALIZED_BYTES and \
                max(pixbuf.get_width(), pixbuf.get_height()) > 640:
            smaller = pixbuf.scale_simple(
                max(1, int(pixbuf.get_width() * 0.8)),
                max(1, int(pixbuf.get_height() * 0.8)),
                GdkPixbuf.InterpType.BILINEAR,
            )
            if smaller is None:
                break
            pixbuf = smaller
            if output_format == 'jpeg':
                saved, data = pixbuf.save_to_bufferv(
                    'jpeg', ['quality'], ['88'])
            else:
                saved, data = pixbuf.save_to_bufferv('png', [], [])
    except ReferenceImageError:
        raise
    except Exception as error:
        raise ReferenceImageError(
            'Could not prepare the pasted image.') from error
    if not saved or not data:
        raise ReferenceImageError('Could not prepare the pasted image.')
    data = bytes(data)
    if len(data) > MAX_NORMALIZED_BYTES:
        raise ReferenceImageError(
            'The pasted image is still too large; copy a simpler image.')
    return ReferenceImage(
        data=data,
        mime_type=mime_type,
        width=pixbuf.get_width(),
        height=pixbuf.get_height(),
        source_name=source_name,
        sha256=hashlib.sha256(data).hexdigest(),
    )


def reference_thumbnail(reference, edge):
    image_type = 'jpeg' if reference.mime_type == 'image/jpeg' else 'png'
    loader = GdkPixbuf.PixbufLoader.new_with_type(image_type)
    loader.write(reference.data)
    loader.close()
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        raise ReferenceImageError('Could not preview that image.')
    width = max(1, pixbuf.get_width())
    height = max(1, pixbuf.get_height())
    scale = min(float(edge) / width, float(edge) / height)
    target_width = max(1, int(round(width * scale)))
    target_height = max(1, int(round(height * scale)))
    return pixbuf.scale_simple(
        target_width, target_height, GdkPixbuf.InterpType.BILINEAR)

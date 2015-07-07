# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, unicode_literals, print_function

import copy
import io
import re

import numpy as np

from . import block
from . import constants
from . import extension
from . import generic_io
from . import reference
from . import schema
from . import treeutil
from . import util
from . import versioning
from . import yamlutil

from .tags.core.asdf import AsdfObject


class AsdfFile(versioning.VersionedMixin):
    """
    The main class that represents a ASDF file.
    """
    def __init__(self, tree=None, uri=None, extensions=None):
        """
        Parameters
        ----------
        tree : dict or AsdfFile, optional
            The main tree data in the ASDF file.  Must conform to the
            ASDF schema.

        uri : str, optional
            The URI for this ASDF file.  Used to resolve relative
            references against.  If not provided, will automatically
            determined from the associated file object, if possible
            and if created from `AsdfFile.open`.

        extensions : list of AsdfExtension
            A list of extensions to the ASDF standard to support when
            reading and writing ASDF files.  See
            `asdftypes.AsdfExtension` for more information.
        """
        if extensions is None or extensions == []:
            self._extensions = extension._builtin_extension_list
        else:
            if isinstance(extensions, extension.AsdfExtensionList):
                self._extensions = extensions
            else:
                if not isinstance(extensions, list):
                    extensions = [extensions]
                extensions.insert(0, extension.BuiltinExtension())
                self._extensions = extension.AsdfExtensionList(extensions)

        self._fd = None
        self._external_asdf_by_uri = {}
        self._blocks = block.BlockManager(self)
        self._uri = None
        if tree is None:
            self.tree = {}
        elif isinstance(tree, AsdfFile):
            if self._extensions != tree._extensions:
                raise ValueError(
                    "Can not copy AsdfFile and change active extensions")
            self._uri = tree.uri
            # Set directly to self._tree (bypassing property), since
            # we can assume the other AsdfFile is already valid.
            self._tree = tree.tree
            self.run_modifying_hook('copy_to_new_asdf', validate=False)
            self.find_references()
        else:
            self.tree = tree
            self.find_references()
        if uri is not None:
            self._uri = uri

    def __enter__(self):
        return self

    def __exit__(self, type, value, traceback):
        if self._fd:
            # This is ok to always do because GenericFile knows
            # whether it "owns" the file and should close it.
            self._fd.__exit__(type, value, traceback)
            self._fd = None
        for external in self._external_asdf_by_uri.values():
            external.__exit__(type, value, traceback)
        self._external_asdf_by_uri.clear()
        self._blocks.close()

    def close(self):
        """
        Close the file handles associated with the `AsdfFile`.
        """
        if self._fd:
            # This is ok to always do because GenericFile knows
            # whether it "owns" the file and should close it.
            self._fd.close()
            self._fd = None
        for external in self._external_asdf_by_uri.values():
            external.close()
        self._external_asdf_by_uri.clear()
        self._blocks.close()

    def copy(self):
        return self.__class__(
            copy.deepcopy(self._tree),
            self._uri,
            self._extensions
        )

    __copy__ = __deepcopy__ = copy

    @property
    def uri(self):
        """
        Get the URI associated with the `AsdfFile`.

        In many cases, it is automatically determined from the file
        handle used to read or write the file.
        """
        if self._uri is not None:
            return self._uri
        if self._fd is not None:
            return self._fd._uri
        return None

    @property
    def tag_to_schema_resolver(self):
        return self._extensions.tag_to_schema_resolver

    @property
    def url_mapping(self):
        return self._extensions.url_mapping

    @property
    def type_index(self):
        return self._extensions.type_index

    def resolve_uri(self, uri):
        """
        Resolve a (possibly relative) URI against the URI of this ASDF
        file.  May be overridden by base classes to change how URIs
        are resolved.  This does not apply any `uri_mapping` that was
        passed to the constructor.

        Parameters
        ----------
        uri : str
            An absolute or relative URI to resolve against the URI of
            this ASDF file.

        Returns
        -------
        uri : str
            The resolved URI.
        """
        return generic_io.resolve_uri(self.uri, uri)

    def open_external(self, uri, fill_defaults=True):
        """
        Open an external ASDF file, from the given (possibly relative)
        URI.  There is a cache (internal to this ASDF file) that ensures
        each external ASDF file is loaded only once.

        Parameters
        ----------
        uri : str
            An absolute or relative URI to resolve against the URI of
            this ASDF file.

        fill_defaults : bool, optional
            When `False`, do not fill in missing default values.
            (Default: `True`).

        Returns
        -------
        asdffile : AsdfFile
            The external ASDF file.
        """
        # For a cache key, we want to ignore the "fragment" part.
        base_uri = util.get_base_uri(uri)
        resolved_uri = self.resolve_uri(base_uri)

        # A uri like "#" should resolve back to ourself.  In that case,
        # just return `self`.
        if resolved_uri == '' or resolved_uri == self.uri:
            return self

        asdffile = self._external_asdf_by_uri.get(resolved_uri)
        if asdffile is None:
            asdffile = self.open(
                resolved_uri,
                fill_defaults=fill_defaults)
            self._external_asdf_by_uri[resolved_uri] = asdffile
        return asdffile

    @property
    def tree(self):
        """
        Get/set the tree of data in the ASDF file.

        When set, the tree will be validated against the ASDF schema.
        """
        return self._tree

    @tree.setter
    def tree(self, tree):
        asdf_object = AsdfObject(tree)
        tagged_tree = yamlutil.custom_tree_to_tagged_tree(
            asdf_object, self)
        schema.validate(tagged_tree, self)
        self._tree = asdf_object

    def validate(self):
        """
        Validate the current state of the tree against the ASDF schema.
        """
        tagged_tree = yamlutil.custom_tree_to_tagged_tree(
            self._tree, self)
        schema.validate(tagged_tree, self)

    def make_reference(self, path=[]):
        """
        Make a new reference to a part of this file's tree, that can be
        assigned as a reference to another tree.

        Parameters
        ----------
        path : list of str and int, optional
            The parts of the path pointing to an item in this tree.
            If omitted, points to the root of the tree.

        Returns
        -------
        reference : reference.Reference
            A reference object.

        Examples
        --------
        For the given AsdfFile ``ff``, add an external reference to the data in
        an external file::

            >>> import pyasdf
            >>> flat = pyasdf.open("http://stsci.edu/reference_files/flat.asdf")  # doctest: +SKIP
            >>> ff.tree['flat_field'] = flat.make_reference(['data'])  # doctest: +SKIP
        """
        return reference.make_reference(self, path)

    @property
    def blocks(self):
        """
        Get the block manager associated with the `AsdfFile`.
        """
        return self._blocks

    def set_array_storage(self, arr, array_storage):
        """
        Set the block type to use for the given array data.

        Parameters
        ----------
        arr : numpy.ndarray
            The array to set.  If multiple views of the array are in
            the tree, only the most recent block type setting will be
            used, since all views share a single block.

        array_storage : str
            Must be one of:

            - ``internal``: The default.  The array data will be
              stored in a binary block in the same ASDF file.

            - ``external``: Store the data in a binary block in a
              separate ASDF file.

            - ``inline``: Store the data as YAML inline in the tree.
        """
        self.blocks[arr].array_storage = array_storage

    def get_array_storage(self, arr):
        """
        Get the block type for the given array data.

        Parameters
        ----------
        arr : numpy.ndarray
        """
        return self.blocks[arr].array_storage

    def set_array_compression(self, arr, compression):
        """
        Set the compression to use for the given array data.

        Parameters
        ----------
        arr : numpy.ndarray
            The array to set.  If multiple views of the array are in
            the tree, only the most recent compression setting will be
            used, since all views share a single block.

        array_compression : str or None
            Must be one of:

            - ``zlib``: Use zlib compression

            - ``bzp2``: Use bzip2 compression

            - ``''`` or `None`: no compression
        """
        self.blocks[arr].compression = compression

    def get_array_compression(self, arr):
        """
        Get the compression type for the given array data.

        Parameters
        ----------
        arr : numpy.ndarray

        Returns
        -------
        compression : str or None
        """
        return self.blocks[arr].compression

    @classmethod
    def _parse_header_line(cls, line):
        """
        Parses the header line in a ASDF file to obtain the ASDF version.
        """
        regex = (constants.ASDF_MAGIC +
                 b'(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<micro>[0-9]+)')
        match = re.match(regex, line)
        if match is None:
            raise ValueError("Does not appear to be a ASDF file.")
        return (int(match.group("major")),
                int(match.group("minor")),
                int(match.group("micro")))

    @classmethod
    def _open_impl(cls, self, fd, uri=None, mode='r',
                   validate_checksums=False,
                   fill_defaults=True,
                   _get_yaml_content=False):
        fd = generic_io.get_file(fd, mode=mode, uri=uri)

        self._fd = fd

        try:
            header_line = fd.read_until(b'\r?\n', 2, "newline", include=True)
        except ValueError:
            raise ValueError("Does not appear to be a ASDF file.")
        self.version = cls._parse_header_line(header_line)

        yaml_token = fd.read(4)
        yaml_content = b''
        tree = {}
        has_blocks = False
        if yaml_token == b'%YAM':
            reader = fd.reader_until(
                constants.YAML_END_MARKER_REGEX, 7, 'End of YAML marker',
                include=True, initial_content=yaml_token)

            if _get_yaml_content:
                yaml_content = reader.read()
            else:
                # We parse the YAML content into basic data structures
                # now, but we don't do anything special with it until
                # after the blocks have been read
                tree = yamlutil.load_tree(reader)
            has_blocks = fd.seek_until(constants.BLOCK_MAGIC, 4, include=True)
        elif yaml_token == constants.BLOCK_MAGIC:
            has_blocks = True
        elif yaml_token != b'':
            raise IOError("ASDF file appears to contain garbage after header.")

        # For testing: just return the raw YAML content
        if _get_yaml_content:
            fd.close()
            return yaml_content

        if has_blocks:
            self._blocks.read_internal_blocks(
                fd, past_magic=True, validate_checksums=validate_checksums)

        tree = reference.find_references(tree, self)
        if fill_defaults:
            schema.fill_defaults(tree, self)
        schema.validate(tree, self)
        tree = yamlutil.tagged_tree_to_custom_tree(tree, self)

        self._tree = tree
        self.run_hook('post_read')

        return self

    @classmethod
    def open(cls, fd, uri=None, mode='r',
             validate_checksums=False,
             extensions=None,
             fill_defaults=True):
        """
        Open an existing ASDF file.

        Parameters
        ----------
        fd : string or file-like object
            May be a string ``file`` or ``http`` URI, or a Python
            file-like object.

        uri : string, optional
            The URI of the file.  Only required if the URI can not be
            automatically determined from `fd`.

        mode : string, optional
            The mode to open the file in.  Must be ``r`` (default) or
            ``rw``.

        validate_checksums : bool, optional
            If `True`, validate the blocks against their checksums.
            Requires reading the entire file, so disabled by default.

        extensions : list of AsdfExtension
            A list of extensions to the ASDF standard to support when
            reading and writing ASDF files.  See
            `asdftypes.AsdfExtension` for more information.

        fill_defaults : bool, optional
            When `False`, do not fill in missing default
            values. (Default: `True`)

        Returns
        -------
        asdffile : AsdfFile
            The new AsdfFile object.
        """
        self = cls(extensions=extensions)

        return cls._open_impl(
            self, fd, uri=uri, mode=mode,
            validate_checksums=validate_checksums,
            fill_defaults=fill_defaults)

    def _write_tree(self, tree, fd, pad_blocks, remove_defaults):
        fd.write(constants.ASDF_MAGIC)
        fd.write(self.version_string.encode('ascii'))
        fd.write(b'\n')

        if len(tree):
            yamlutil.dump_tree(tree, fd, self, remove_defaults)

        if pad_blocks:
            padding = util.calculate_padding(
                fd.tell(), pad_blocks, fd.block_size)
            fd.fast_forward(padding)

    def _pre_write(self, fd, all_array_storage, all_array_compression,
                   auto_inline):
        self._all_array_storage = all_array_storage
        self._all_array_compression = all_array_compression
        self._auto_inline = auto_inline

        if len(self._tree):
            self.run_hook('pre_write')

        # This is where we'd do some more sophisticated block
        # reorganization, if necessary
        self._blocks.finalize(self)

    def _serial_write(self, fd, pad_blocks, remove_defaults):
        self._write_tree(self._tree, fd, pad_blocks, remove_defaults)
        self.blocks.write_internal_blocks_serial(fd, pad_blocks)
        self.blocks.write_external_blocks(fd.uri, pad_blocks)

    def _random_write(self, fd, pad_blocks, remove_defaults):
        self._write_tree(self._tree, fd, False, remove_defaults)
        self.blocks.write_internal_blocks_random_access(fd)
        self.blocks.write_external_blocks(fd.uri, pad_blocks)

    def _post_write(self, fd):
        if len(self._tree):
            self.run_hook('post_write')

        if hasattr(self, '_all_array_storage'):
            del self._all_array_storage
        if hasattr(self, '_all_array_compression'):
            del self._all_array_compression
        if hasattr(self, '_auto_inline'):
            del self._auto_inline

    def update(self, all_array_storage=None, all_array_compression=None,
               auto_inline=None, pad_blocks=False, remove_defaults=True):
        """
        Update the file on disk in place.

        Parameters
        ----------
        all_array_storage : string, optional
            If provided, override the array storage type of all blocks
            in the file immediately before writing.  Must be one of:

            - ``internal``: The default.  The array data will be
              stored in a binary block in the same ASDF file.

            - ``external``: Store the data in a binary block in a
              separate ASDF file.

            - ``inline``: Store the data as YAML inline in the tree.

        all_array_compression : string, optional
            If provided, set the compression type on all binary blocks
            in the file.  Must be one of:

            - ``''``: No compression.

            - ``zlib``: Use zlib compression.

            - ``bzp2``: Use bzip2 compression.

        auto_inline : int, optional
            When the number of elements in an array is less than this
            threshold, store the array as inline YAML, rather than a
            binary block.  This only works on arrays that do not share
            data with other arrays.  Default is 0.

        pad_blocks : float or bool, optional
            Add extra space between blocks to allow for updating of
            the file.  If `False` (default), add no padding (always
            return 0).  If `True`, add a default amount of padding of
            10% If a float, it is a factor to multiple content_size by
            to get the new total size.

        remove_defaults : bool, optional
            When `False`, do not remove values that are set to the
            default in the schema.  (Default: `True`)
        """
        fd = self._fd

        if fd is None:
            raise ValueError(
                "Can not update, since there is no associated file")

        if not fd.writable():
            raise IOError(
                "Can not update, since associated file is read-only")

        if all_array_storage == 'external':
            # If the file is fully exploded, there's no benefit to
            # update, so just use write_to()
            self.write_to(fd, all_array_storage=all_array_storage)
            fd.truncate(fd.tell())
            return

        if not fd.seekable():
            raise IOError(
                "Can not update, since associated file is not seekable")

        self._pre_write(fd, all_array_storage, all_array_compression,
                        auto_inline)

        try:
            fd.seek(0)

            if not self.blocks.has_blocks_with_offset():
                # If we don't have any blocks that are being reused, just
                # write out in a serial fashion.
                self._serial_write(fd, pad_blocks, remove_defaults)
                fd.truncate(fd.tell())
                return

            # Estimate how big the tree will be on disk by writing the
            # YAML out in memory.  Since the block indices aren't yet
            # known, we have to count the number of block references and
            # add enough space to accommodate the largest block number
            # possible there.
            tree_serialized = io.BytesIO()
            self._write_tree(self._tree, tree_serialized, pad_blocks=False,
                             remove_defaults=remove_defaults)
            array_ref_count = [0]
            from .tags.core.ndarray import NDArrayType

            for node in treeutil.iter_tree(self._tree):
                if (isinstance(node, (np.ndarray, NDArrayType)) and
                    self.blocks[node].array_storage == 'internal'):
                    array_ref_count[0] += 1

            serialized_tree_size = (
                tree_serialized.tell() +
                constants.MAX_BLOCKS_DIGITS * array_ref_count[0])

            if not block.calculate_updated_layout(
                    self.blocks, serialized_tree_size,
                    pad_blocks, fd.block_size):
                # If we don't have any blocks that are being reused, just
                # write out in a serial fashion.
                self._serial_write(fd, pad_blocks, remove_defaults)
                fd.truncate(fd.tell())
                return

            fd.seek(0)
            self._random_write(fd, pad_blocks, remove_defaults)
            fd.flush()
        finally:
            self._post_write(fd)

    def write_to(self, fd, all_array_storage=None, all_array_compression=None,
                 auto_inline=None, pad_blocks=False, remove_defaults=True):
        """
        Write the ASDF file to the given file-like object.

        `write_to` does not change the underlying file descriptor in
        the `AsdfFile` object, but merely copies the content to a new
        file.

        Parameters
        ----------
        fd : string or file-like object
            May be a string path to a file, or a Python file-like
            object.  If a string path, the file is automatically
            closed after writing.  If not a string path,

        all_array_storage : string, optional
            If provided, override the array storage type of all blocks
            in the file immediately before writing.  Must be one of:

            - ``internal``: The default.  The array data will be
              stored in a binary block in the same ASDF file.

            - ``external``: Store the data in a binary block in a
              separate ASDF file.

            - ``inline``: Store the data as YAML inline in the tree.

        all_array_compression : string, optional
            If provided, set the compression type on all binary blocks
            in the file.  Must be one of:

            - ``''``: No compression.

            - ``zlib``: Use zlib compression.

            - ``bzp2``: Use bzip2 compression.

        auto_inline : int, optional
            When the number of elements in an array is less than this
            threshold, store the array as inline YAML, rather than a
            binary block.  This only works on arrays that do not share
            data with other arrays.  Default is 0.

        pad_blocks : float or bool, optional
            Add extra space between blocks to allow for updating of
            the file.  If `False` (default), add no padding (always
            return 0).  If `True`, add a default amount of padding of
            10% If a float, it is a factor to multiple content_size by
            to get the new total size.

        remove_defaults : bool, optional
            When `False`, do not remove values that are set to the
            default in the schema.  (Default: `True`)
        """
        original_fd = self._fd

        try:
            with generic_io.get_file(fd, mode='w') as fd:
                self._fd = fd
                self._pre_write(fd, all_array_storage, all_array_compression,
                                auto_inline)

                try:
                    self._serial_write(fd, pad_blocks, remove_defaults)
                    fd.flush()
                finally:
                    self._post_write(fd)
        finally:
            self._fd = original_fd

    def find_references(self):
        """
        Finds all external "JSON References" in the tree and converts
        them to `reference.Reference` objects.
        """
        # Set directly to self._tree, since it doesn't need to be
        # re-validated.
        self._tree = reference.find_references(self._tree, self)

    def resolve_references(self):
        """
        Finds all external "JSON References" in the tree, loads the
        external content, and places it directly in the tree.  Saving
        a ASDF file after this operation means it will have no
        external references, and will be completely self-contained.
        """
        # Set to the property self.tree so the resulting "complete"
        # tree will be validated.
        self.tree = reference.resolve_references(self._tree, self)

    def run_hook(self, hookname):
        """
        Run a "hook" for each custom type found in the tree.

        Parameters
        ----------
        hookname : str
            The name of the hook.  If a `AsdfType` is found with a method
            with this name, it will be called for every instance of the
            corresponding custom type in the tree.
        """
        if not self.type_index.has_hook(hookname):
            return

        for node in treeutil.iter_tree(self._tree):
            tag = self.type_index.from_custom_type(type(node))
            if tag is not None:
                hook = getattr(tag, hookname, None)
                if hook is not None:
                    hook(node, self)

    def run_modifying_hook(self, hookname, validate=True):
        """
        Run a "hook" for each custom type found in the tree.  The hook
        is free to return a different object in order to modify the
        tree.

        Parameters
        ----------
        hookname : str
            The name of the hook.  If a `AsdfType` is found with a method
            with this name, it will be called for every instance of the
            corresponding custom type in the tree.

        validate : bool
            When `True` (default) validate the resulting tree.
        """
        def walker(node):
            tag = self.type_index.from_custom_type(type(node))
            if tag is not None:
                hook = getattr(tag, hookname, None)
                if hook is not None:
                    return hook(node, self)
            return node
        tree = treeutil.walk_and_modify(self.tree, walker)
        if validate:
            self.tree = tree
        else:
            self._tree = tree
        return self._tree

    def resolve_and_inline(self):
        """
        Resolves all external references and inlines all data.  This
        produces something that, when saved, is a 100% valid YAML
        file.
        """
        self.resolve_references()
        for b in self.blocks.blocks:
            b.array_storage = 'inline'

    def fill_defaults(self):
        """
        Fill in any values that are missing in the tree using default
        values from the schema.
        """
        tree = yamlutil.custom_tree_to_tagged_tree(self._tree, self)
        schema.fill_defaults(tree, self)
        self._tree = yamlutil.tagged_tree_to_custom_tree(tree, self)

    def remove_defaults(self):
        """
        Remove any values in the tree that are the same as the default
        values in the schema
        """
        tree = yamlutil.custom_tree_to_tagged_tree(self._tree, self)
        schema.remove_defaults(tree, self)
        self._tree = yamlutil.tagged_tree_to_custom_tree(tree, self)

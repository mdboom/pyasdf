# Licensed under a 3-clause BSD style license - see LICENSE.rst
# -*- coding: utf-8 -*-

from __future__ import absolute_import, division, unicode_literals, print_function


class UserSet(object):
    def __init___(self, data=None):
        if data is None:
            data = set()
        self.data = data

    def __len__(self):
        return len(self.data)

    def __contains__(self, x):
        return x in self.data

    def isdisjoint(self, t):
        return self.data.isdisjoint(t)

    def issubset(self, t):
        return self.data.issubset(t)

    def __leq__(self, t):
        return self.data <= t

    def __lt__(self, t):
        return self.data < t

    def issuperset(self, t):
        return self.data.issuperset(t)

    def __geq__(self, t):
        return self.data >= t

    def __gt__(self, t):
        return self.data > t

    def union(self, *args):
        return self.data.union(*args)

    def __or__(self, t):
        return self.data | t

    def intersection(self, *args):
        return self.data.intersection(*args)

    def __and__(self, t):
        return self.data & t

    def difference(self, *args):
        return self.data.difference(*args)

    def __sub__(self, t):
        return self.data - t

    def symmetric_difference(self, *args):
        return self.data.symmetric_difference(*args)

    def __xor__(self, t):
        return self.data ^ t

    def copy(self):
        return self.data.copy()

    def update(self, *args):
        return self.data.update(*args)

    def __ior__(self, t):
        self.data |= t

    def intersection_update(self, *args):
        return self.data.intersection_update(*args)

    def __iand__(self, t):
        self.data &= t

    def difference_update(self, *args):
        return self.data.difference_update(*args)

    def __isub__(self, t):
        self.data -= t

    def symmetric_difference_update(self, *args):
        return self.data.symmetric_difference_update(*args)

    def __ixor__(self, t):
        self.data ^= t

    def add(self, elem):
        return self.data.add(elem)

    def remove(self, elem):
        return self.data.remove(elem)

    def discard(self, elem):
        return self.data.discard(elem)

    def pop(self):
        return self.data.pop()

    def clear(self):
        return self.data.clear()

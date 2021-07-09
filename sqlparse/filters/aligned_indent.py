#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

from sqlparse import sql, tokens as T
from sqlparse.utils import offset, indent
from sqlparse.filters.statement_sections_splitter import SSSplitterFilter


class AlignedIndentFilter(SSSplitterFilter):

    def __init__(self, char=' ', n='\n', width=1):
        super().__init__(char, n)
        self.width = width

    def _get_kwd_offset(self, token):
        token_indent = token.value
        if (
            token.match(T.Keyword, self.join_words, regex=True)
            or token.match(T.Keyword, self.by_words, regex=True)
        ):
            token_indent = token_indent.split()[0]
        token_indent = max(token.parent.opening_keyword_length - len(token_indent), 0)
        return token_indent

    def _get_section_offset(self, token):
        section_offset = max(token.parent.opening_keyword_length - token.opening_keyword_length, 0)
        return section_offset

    def _process_identifierlist(self, tlist, max_id_list_count=0):

        if tlist.id_list_count > max_id_list_count and not tlist.within(sql.Function) and not tlist.within(sql.Values):
            identifiers = list(tlist.get_identifiers())
            identifiers.pop(0)
            num_offset = 1 if self.char == '\t' else 0
            with offset(self, num_offset):
                for token in identifiers:
                    tlist.insert_before(token, self.nl())
        self._process_default(tlist)

    def _process_clausefrom(self, tlist):
        section_offset = self._get_section_offset(tlist)
        with offset(self, section_offset):
            self._process_default(tlist)

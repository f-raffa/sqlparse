#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

from sqlparse import sql, tokens as T
from sqlparse.utils import offset, indent
from sqlparse.filters.statement_sections_splitter import SSSplitterFilter


class ReindentFilter(SSSplitterFilter):

    def __init__(self, width=2, char=' ', wrap_after=0, n='\n',
                 comma_first=False, indent_after_first=False,
                 indent_columns=False):
        super().__init__(char, n)
        self.indent = 1 if indent_after_first else 0
        self.width = width
        self.wrap_after = wrap_after
        self.comma_first = comma_first
        self.indent_columns = indent_columns

    def _get_kwd_offset(self, token):
        return 0

    def _get_section_offset(self, token):
        return 0

    def _process_identifierlist(self, tlist, max_id_list_count=0):
        identifiers = list(tlist.get_identifiers())
        identifiers.pop(0)
        if self.indent_columns:
            num_offset = 1 if self.char == '\t' else self.width
        else:
            if self.char == '\t':
                num_offset = 1
            else:
                num_offset = 0

        if tlist.id_list_count > max_id_list_count and not tlist.within(sql.Function) and not tlist.within(sql.Values):
            with offset(self, num_offset):
                position = 0
                for token in identifiers:
                    # Add 1 for the "," separator
                    position += len(token.value) + 1
                    if position > (self.wrap_after - self.offset):
                        tidx = tlist.token_index(token)
                        adjust = 0
                        if self.comma_first:
                            adjust = -2
                            _, comma = tlist.token_prev(tidx)
                            if comma is None:
                                continue
                            token = comma
                        tlist.insert_before(token, self.nl(offset=adjust))
                        if self.comma_first:
                            _, ws = tlist.token_next(tidx, skip_ws=False)
                            if ws is not None and ws.ttype is not T.Text.Whitespace:
                                tlist.insert_after(token, sql.Token(T.Whitespace, ' '))
                        position = 0
        elif tlist.id_list_count > max_id_list_count:
            # ensure whitespace
            # for token in tlist:
            #     _, next_ws = tlist.token_next(
            #         tlist.token_index(token), skip_ws=False)
            #     if token.value == ',' and not next_ws.is_whitespace:
            #         tlist.insert_after(
            #             token, sql.Token(T.Whitespace, ' '))

            end_at = self.offset + sum(len(i.value) + 1 for i in identifiers)
            adjusted_offset = 0
            if (self.wrap_after > 0
                    and end_at > (self.wrap_after - self.offset)
                    and self._last_func):
                adjusted_offset = -len(self._last_func.value) - 1

            with offset(self, adjusted_offset), indent(self):
                if adjusted_offset < 0:
                    tlist.insert_before(identifiers[0], self.nl())
                position = 0
                for token in identifiers:
                    # Add 1 for the "," separator
                    position += len(token.value) + 1
                    if self.wrap_after > 0 and position > (self.wrap_after - self.offset):
                        adjust = 0
                        tlist.insert_before(token, self.nl(offset=adjust))
                        position = 0
        self._process_default(tlist)

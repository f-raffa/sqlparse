#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

from sqlparse import sql, tokens as T
from sqlparse.utils import offset
import sys, traceback


class SSSplitterFilter:
    join_words = (r'((LEFT\s+|RIGHT\s+|FULL\s+)?'
                  r'(INNER\s+|OUTER\s+|STRAIGHT\s+)?|'
                  r'(CROSS\s+|NATURAL\s+)?)?JOIN\b')
    by_words = r'(GROUP|ORDER)\s+BY\b'
    split_words = (join_words, by_words, 'AND', 'INTO',
                   'OR', 'HAVING', 'LIMIT', 'UNION',
                   'VALUES', 'SET', 'BETWEEN', 'EXCEPT')

    def __init__(self, char=' ', n='\n'):
        self.n = n
        self.offset = 0
        self.indent = 0
        self.width = 0
        self.char = char

        self._last_func = None
        self._curr_stmt = None

    @property
    def leading_ws(self):
        return self.offset + self.indent * self.width

    def _flatten_up_to_token(self, token):
        """Yields all tokens up to token but excluding current."""
        if token.is_group:
            token = next(token.flatten())

        for t in self._curr_stmt.flatten():
            if t == token:
                break
            yield t

    def _get_token_offset(self, token):
        raw = ''.join(map(str, self._flatten_up_to_token(token)))
        line = (raw or '\n').splitlines()[-1]
        # Now take current offset into account and return relative offset.
        return len(line) - len(self.char * self.leading_ws)

    def nl(self, offset=0):
        return sql.Token(
            T.Whitespace,
            self.n + self.char * max(0, self.leading_ws + offset))

    def _next_token(self, tlist, idx=-1):
        split_words = T.Keyword, self.split_words, True
        tidx, token = tlist.token_next_by(m=split_words, idx=idx)

        if token and token.normalized == 'BETWEEN':
            tidx, token = self._next_token(tlist, tidx)

            if token and token.normalized == 'AND':
                tidx, token = self._next_token(tlist, tidx)

        return tidx, token

    def process(self, stmt):
        self._curr_stmt = stmt
        self._process(stmt)
        return stmt

    def _process(self, tlist):
        func_name = '_process_{cls}'.format(cls=type(tlist).__name__)
        func = getattr(self, func_name.lower(), self._process_default)
        try:
            func(tlist)
        except Exception as e:
            print("######################################\n\n\n")
            print("{0}: {1} formatting \n{2}".format(str(e.__class__), e, tlist.value))
            exc_type, exc_value, exc_traceback = sys.exc_info()
            print("*** print_tb:")
            traceback.print_tb(exc_traceback, limit=15, file=sys.stdout)


        # import sys, traceback
        #
        # def lumberjack():
        #     bright_side_of_death()
        #
        # def bright_side_of_death():
        #     return tuple()[0]
        #
        # try:
        #     lumberjack()
        # except IndexError:
        #     exc_type, exc_value, exc_traceback = sys.exc_info()
        #     print("*** print_tb:")
        #     traceback.print_tb(exc_traceback, limit=1, file=sys.stdout)
        #     print("*** print_exception:")
        #     # exc_type below is ignored on 3.5 and later
        #     traceback.print_exception(exc_type, exc_value, exc_traceback,
        #                               limit=2, file=sys.stdout)
        #     print("*** print_exc:")
        #     traceback.print_exc(limit=2, file=sys.stdout)
        #     print("*** format_exc, first and last line:")
        #     formatted_lines = traceback.format_exc().splitlines()
        #     print(formatted_lines[0])
        #     print(formatted_lines[-1])
        #     print("*** format_exception:")
        #     # exc_type below is ignored on 3.5 and later
        #     print(repr(traceback.format_exception(exc_type, exc_value,
        #                                           exc_traceback)))
        #     print("*** extract_tb:")
        #     print(repr(traceback.extract_tb(exc_traceback)))
        #     print("*** format_tb:")
        #     print(repr(traceback.format_tb(exc_traceback)))
        #     print("*** tb_lineno:", exc_traceback.tb_lineno)

    def _process_function(self, tlist):
        self._last_func = tlist[0]
        self._process_default(tlist)

    def _process_parenthesis(self, tlist):
        # _, token = tlist.token_next(idx=0)
        # is_dml_dll = imt(token, i=sql.Statement)
        if tlist.is_codeBlockDelimiter:
            tlist.insert_before(-1, self.nl())
            indentation_offset = 4
            tlist.insert_before(1, self.nl(offset=indentation_offset))
        elif isinstance(tlist.parent, sql.ClauseInsert):
            indentation_offset = len('SELECT ') # = len()
            tlist.insert_before(1, self.nl(offset=indentation_offset))
            tlist.insert_before(-1, sql.Token(T.Whitespace, ' '))
        else:
            fidx, first = tlist.token_next_by(m=sql.Parenthesis.M_OPEN)
            indentation_offset = self._get_token_offset(first) + 1

            if ( not isinstance(tlist.parent, (sql.Function, sql.WindowFunction, sql.Comparison))
                 or (isinstance(tlist.parent, sql.Comparison) and isinstance(tlist.tokens[1], sql.Statement)) ):
                indentation_offset += 1
                tlist.insert_before(1, sql.Token(T.Whitespace, ' '))
                tlist.insert_before(-1, sql.Token(T.Whitespace, ' '))

        with offset(self, indentation_offset):
            for token in tlist[1:-1]:
                if token.is_group:
                    if isinstance(token, sql.IdentifierList):
                        self._process_identifierlist(token, 2)
                    else:
                        self._process(token)

    def _process_case(self, tlist):
        iterable = iter(tlist.get_cases())
        cond, _ = next(iterable)
        first = next(cond[0].flatten())

        with offset(self, self._get_token_offset(tlist[0])):
            with offset(self, self._get_token_offset(first)):
                for cond, value in iterable:
                    token = value[0] if cond is None else cond[0]
                    tlist.insert_before(token, self.nl())

                # Line breaks on group level are done. let's add an offset of
                # len "when ", "then ", "else "
            with offset(self, len("WHEN")):
                self._process_default(tlist)
            end_idx, end = tlist.token_next_by(m=sql.Case.M_CLOSE)
            if end_idx is not None:
                tlist.insert_before(end_idx, self.nl())

    def _process_conditionslist(self, tlist):
        if tlist.conditions_count > 2:
            with offset(self, self._get_token_offset(tlist[0])):
                self._split_kwds(tlist)

        for sgroup in tlist.get_sublists():
            self._process(sgroup)  # =_process_conditionslist(tlist):

    def _process_clauseidentifierslist(self, tlist, max_id_list_count=0):
        id_list = tlist.get_identifiers_list()

        if id_list:
            with offset(self, tlist.opening_keyword_length + 1):
                if isinstance(id_list, sql.IdentifierList):
                    self._process_identifierlist(id_list, max_id_list_count)
                else:
                    self._process(id_list)

    def _process_selectprojection(self, tlist):
        self._process_clauseidentifierslist(tlist, max_id_list_count=0)

    def _process_clausewith(self, tlist):
        self._process_clauseidentifierslist(tlist, max_id_list_count=0)

    def _process_clausepartitionby(self, tlist):
        self._process_clauseidentifierslist(tlist, max_id_list_count=2)

    def _process_clauseorderby(self, tlist):
        self._process_clauseidentifierslist(tlist, max_id_list_count=2)

    def _process_clausegroupby(self, tlist):
        self._process_clauseidentifierslist(tlist, max_id_list_count=2)


    def _process_values(self, tlist):
        tlist.insert_before(0, self.nl())
        tidx, token = tlist.token_next_by(i=sql.Parenthesis)
        first_token = token
        while token:
            ptidx, ptoken = tlist.token_next_by(m=(T.Punctuation, ','),
                                                idx=tidx)
            if ptoken:
                if self.comma_first:
                    adjust = -2
                    offset = self._get_token_offset(first_token) + adjust
                    tlist.insert_before(ptoken, self.nl(offset))
                else:
                    tlist.insert_after(ptoken,
                                       self.nl(self._get_token_offset(token)))
            tidx, token = tlist.token_next_by(i=sql.Parenthesis, idx=tidx)

    def _process_statement(self, tlist):
        self._split_sections(tlist)
        self._process_default(tlist)

    def _process_statementunion(self, tlist):
        self._process_statement(tlist)

    def _process_statementinsert(self, tlist):
        self._process_statement(tlist)

    def _process_statementselect(self, tlist):
        self._process_statement(tlist)

    def _process_default(self, tlist):
        self._split_kwds(tlist)

        for sgroup in tlist.get_sublists():
            self._process(sgroup)

    def _split_kwds(self, tlist):
        tidx, token = self._next_token(tlist)
        while token:
            token_indent = self._get_kwd_offset(token)
            tlist.insert_before(token, self.nl(token_indent))
            tidx += 1
            tidx, token = self._next_token(tlist, tidx)

    def _split_sections(self, tlist):
        nested_statements = list(tlist.get_sections())
        if nested_statements and (not tlist.parent
                                  or not tlist.parent.parent
                                  or isinstance(nested_statements[0], (sql.SelectProjection, sql.StatementSelect))):
            nested_statements.pop(0)

        for token in nested_statements:
            section_offset = self._get_section_offset(token)
            tlist.insert_before(token, self.nl(section_offset))

#
# Copyright (C) 2009-2020 the sqlparse authors and contributors
# <see AUTHORS file>
#
# This module is part of python-sqlparse and is released under
# the BSD License: https://opensource.org/licenses/BSD-3-Clause

from sqlparse import sql
from sqlparse import tokens as T
from sqlparse.utils import recurse, imt, consume
from sqlparse.exceptions import SQLParseError
import sys, traceback

T_NUMERICAL = (T.Number, T.Number.Integer, T.Number.Float)
T_STRING = (T.String, T.String.Single, T.String.Symbol)
T_NAME = (T.Name, T.Name.Placeholder)


def _group_matching(tlist, cls):
    """Groups Tokens that have beginning and end."""
    opens = []
    tidx_offset = 0
    for idx, token in enumerate(list(tlist)):
        tidx = idx - tidx_offset

        if token.is_whitespace:
            # ~50% of tokens will be whitespace. Will checking early
            # for them avoid 3 comparisons, but then add 1 more comparison
            # for the other ~50% of tokens...
            continue

        if token.is_group and not isinstance(token, cls):
            # Check inside previously grouped (i.e. parenthesis) if group
            # of different type is inside (i.e., case). though ideally  should
            # should check for all open/close tokens at once to avoid recursion
            _group_matching(token, cls)
            continue

        if token.match(*cls.M_OPEN):
            opens.append(tidx)

        elif token.match(*cls.M_CLOSE):
            try:
                open_idx = opens.pop()
            except IndexError:
                # this indicates invalid sql and unbalanced tokens.
                # instead of break, continue in case other "valid" groups exist
                continue
            close_idx = tidx
            tlist.group_tokens(cls, open_idx, close_idx)
            tidx_offset += close_idx - open_idx


def group_brackets(tlist):
    _group_matching(tlist, sql.SquareBrackets)


def group_parenthesis(tlist):
    cls = sql.Parenthesis
    m_block_discriminant = (T.Keyword, ('THEN', 'AS'))
    m_subqry_discriminant = (T.Keyword.DML, 'SELECT')

    opens = []
    is_blocks = []
    is_subqry = []
    tidx_offset = 0
    for idx, token in enumerate(list(tlist)):
        tidx = idx - tidx_offset

        if token.is_whitespace:
            continue

        if token.is_group and not isinstance(token, cls):
            group_parenthesis(token)
            continue

        if token.match(*cls.M_OPEN):
            opens.append(tidx)
            ibdis, tbdis = tlist.token_prev(idx=tidx)
            if tbdis and tbdis.match(*m_block_discriminant):
                is_blocks.append(True)
            else:
                is_blocks.append(False)
            isbdis, tsbdis = tlist.token_next(idx=tidx)
            if tsbdis and tsbdis.match(*m_subqry_discriminant):
                is_subqry.append(True)
            else:
                is_subqry.append(False)
        elif token.match(*cls.M_CLOSE):
            try:
                open_idx = opens.pop()
                close_idx = tidx
                tlist.group_tokens(cls, open_idx, close_idx)
                if is_blocks.pop():
                    tlist.tokens[open_idx].is_codeBlockDelimiter = True
                if is_subqry.pop():
                    tlist.tokens[open_idx].is_subQuery = True
                tidx_offset += close_idx - open_idx
            except IndexError:
                raise SQLParseError('Unbalanced parenthesis found in the statement.')


def group_case(tlist):
    _group_matching(tlist, sql.Case)


def group_if(tlist):
    _group_matching(tlist, sql.If)


def group_for(tlist):
    _group_matching(tlist, sql.For)


def group_begin(tlist):
    _group_matching(tlist, sql.Begin)


def group_typecasts(tlist):
    def match(token):
        return token.match(T.Punctuation, '::')

    def valid(token):
        return token is not None

    def post(tlist, pidx, tidx, nidx):
        return pidx, nidx

    valid_prev = valid_next = valid
    _group(tlist, sql.Identifier, match, valid_prev, valid_next, post)


def group_tzcasts(tlist):
    def match(token):
        return token.ttype == T.Keyword.TZCast

    def valid(token):
        return token is not None

    def post(tlist, pidx, tidx, nidx):
        return pidx, nidx

    _group(tlist, sql.Identifier, match, valid, valid, post)


def group_typed_literal(tlist):
    # definitely not complete, see e.g.:
    # https://docs.microsoft.com/en-us/sql/odbc/reference/appendixes/interval-literal-syntax
    # https://docs.microsoft.com/en-us/sql/odbc/reference/appendixes/interval-literals
    # https://www.postgresql.org/docs/9.1/datatype-datetime.html
    # https://www.postgresql.org/docs/9.1/functions-datetime.html
    def match(token):
        return imt(token, m=sql.TypedLiteral.M_OPEN)

    def match_to_extend(token):
        return isinstance(token, sql.TypedLiteral)

    def valid_prev(token):
        return token is not None

    def valid_next(token):
        return token is not None and token.match(*sql.TypedLiteral.M_CLOSE)

    def valid_final(token):
        return token is not None and token.match(*sql.TypedLiteral.M_EXTEND)

    def post(tlist, pidx, tidx, nidx):
        return tidx, nidx

    _group(tlist, sql.TypedLiteral, match, valid_prev, valid_next,
           post, extend=False)
    _group(tlist, sql.TypedLiteral, match_to_extend, valid_prev, valid_final,
           post, extend=True)


def group_period(tlist):
    def match(token):
        return token.match(T.Punctuation, '.')

    def valid(token):
        sqlcls = sql.SquareBrackets, sql.Identifier
        ttypes = T.Name, T.String.Symbol, T.Name.Placeholder
        return imt(token, i=sqlcls, t=ttypes)

    def post(tlist, pidx, tidx, nidx):
        return (pidx, nidx)

    valid_prev = valid_next = valid
    _group(tlist, sql.Identifier, match, valid_prev, valid_next, post)


def group_as(tlist):
    def match(token):
        return token.is_keyword and token.normalized == 'AS'

    def valid_prev(token):
        if ( token.normalized == 'NULL'
             or not token.is_keyword
             or (isinstance(token, sql.Parenthesis) and not token.is_subQuery)
        ):
            is_valid = True
        else:
            is_valid = False
        return is_valid
        # return token.normalized == 'NULL' or ( not token.is_keyword and not isinstance(token, sql.Parenthesis))

    def valid_next(token):
        ttypes = T.DML, T.DDL, T.CTE
        if (token is not None
            and not imt(token, t=ttypes)
            and (not isinstance(token, sql.Parenthesis) or not token.is_subQuery)
        ):
            is_valid = True
        else:
            is_valid = False

        return is_valid
        # return token is not None and not imt(token, t=ttypes) and not isinstance(token, sql.Parenthesis)

    def post(tlist, pidx, tidx, nidx):
        return pidx, nidx

    _group(tlist, sql.Identifier, match, valid_prev, valid_next, post)


def group_assignment(tlist):
    def match(token):
        return token.match(T.Assignment, ':=')

    def valid(token):
        return token is not None and token.ttype not in (T.Keyword)

    def post(tlist, pidx, tidx, nidx):
        m_semicolon = T.Punctuation, ';'
        snidx, _ = tlist.token_next_by(m=m_semicolon, idx=nidx)
        nidx = snidx or nidx
        return pidx, nidx

    valid_prev = valid_next = valid
    _group(tlist, sql.Assignment, match, valid_prev, valid_next, post)


def group_comparison(tlist):
    sqlcls = (sql.Parenthesis, sql.Function, sql.Identifier,
              sql.Operation, sql.TypedLiteral)
    ttypes = T_NUMERICAL + T_STRING + T_NAME + (T.Name.Builtin,)

    def match(token):
        return token.ttype == T.Operator.Comparison

    def valid(token):
        if imt(token, t=ttypes, i=sqlcls):
            return True
        elif token and token.is_keyword and token.normalized == 'NULL':
            return True
        else:
            return False

    def post(tlist, pidx, tidx, nidx):
        return pidx, nidx

    valid_prev = valid_next = valid
    _group(tlist, sql.Comparison, match,
           valid_prev, valid_next, post, extend=False)


@recurse(sql.Identifier)
def group_identifier(tlist):
    ttypes = (T.String.Symbol, T.Name)

    # Placeholders Grouping
    tidx, token = tlist.token_next_by(t=T.Name.Placeholder)
    while tidx:
        from_idx = to_idx = tidx
        nidx, n_token = tlist.token_next(idx=tidx, skip_ws=False)
        pidx, p_token = tlist.token_next(idx=tidx, skip_ws=False, reverse=True)

        if pidx and imt(p_token, t=ttypes, i=sql.Identifier):
            from_idx = pidx

        if n_token and imt(n_token, t=ttypes, i=sql.Identifier):
            to_idx = nidx
        tlist.group_tokens(sql.Identifier, from_idx, to_idx)

        tidx, token = tlist.token_next_by(idx=tidx, t=T.Name.Placeholder)
    # Placeholders Grouping Done

    tidx, token = tlist.token_next_by(t=ttypes)
    while token:
        tlist.group_tokens(sql.Identifier, tidx, tidx)
        tidx, token = tlist.token_next_by(t=ttypes, idx=tidx)


@recurse(sql.SignedIdentifier)
def group_signed_identifier(tlist):
    tpatt = (T.Operator, '-', False)

    tidx, token = tlist.token_next_by(t=tpatt)
    while tidx:
        nidx, n_token = tlist.token_next(idx=tidx)
        pidx, p_token = tlist.token_next(idx=tidx, reverse=True)
        if ( n_token and isinstance(n_token, sql.Identifier)
             and ( not pidx or not isinstance(p_token, sql.Identifier))
        ):
            tlist.group_tokens(sql.SignedIdentifier, tidx, nidx)
        tidx, token = tlist.token_next_by(idx=tidx, t=tpatt)


def group_conditions_list(tlist):
    sqlcls = (sql.Comparison, sql.Parenthesis, sql.ConditionsList, sql.Identifier)
    iclss = sqlcls + (sql.Comment,)
    mtypes = (T.Keyword, 'NOT', False)

    def match(token):
        return token.match(T.Keyword, ('AND', 'OR'), False)

    def valid(token):
        return imt(token, i=iclss, m=mtypes)

    def post(tlist, pidx, tidx, nidx):
        from_idx, to_idx = pidx, nidx

        ptoken = tlist.tokens[from_idx]
        ntoken = tlist.tokens[to_idx]

        if isinstance(ptoken, sql.Comment):
            from_idx, ptoken = tlist.token_next_by(idx=from_idx, i=sqlcls, reverse=True)

        if isinstance(ntoken, sql.Comment) or imt(ntoken, m=mtypes):
            to_idx, ntoken = tlist.token_next_by(idx=to_idx, i=sqlcls)

        if isinstance(ptoken, sql.ConditionsList):
            increment = 1
            if isinstance(ntoken, sql.Parenthesis):
                increment = 2
            ptoken.conditions_count += increment

        return from_idx, to_idx

    valid_prev = valid_next = valid
    _group(tlist, sql.ConditionsList, match,
           valid_prev, valid_next, post, extend=True)


def group_arrays(tlist):
    sqlcls = sql.SquareBrackets, sql.Identifier, sql.Function
    ttypes = T.Name, T.String.Symbol

    def match(token):
        return isinstance(token, sql.SquareBrackets)

    def valid_prev(token):
        return imt(token, i=sqlcls, t=ttypes)

    def valid_next(token):
        return True

    def post(tlist, pidx, tidx, nidx):
        return pidx, tidx

    _group(tlist, sql.Identifier, match,
           valid_prev, valid_next, post, extend=True, recurse=False)


def group_operator(tlist):
    ttypes = T_NUMERICAL + T_STRING + T_NAME
    sqlcls = (sql.SquareBrackets, sql.Parenthesis, sql.Function,
              sql.Identifier, sql.Operation, sql.TypedLiteral)

    def match(token):
        return imt(token, t=(T.Operator, T.Wildcard))

    def valid(token):
        return imt(token, i=sqlcls, t=ttypes) \
            or (token and token.match(
                T.Keyword,
                ('CURRENT_DATE', 'CURRENT_TIME', 'CURRENT_TIMESTAMP')))

    def post(tlist, pidx, tidx, nidx):
        tlist[tidx].ttype = T.Operator

        sign_idx, sign_token = tlist.token_next(idx=pidx, skip_cm=False, reverse=True)
        if sign_token and sign_token.match(T.Operator, ('-', '+'), False):
            from_idx = sign_idx
        else:
            from_idx = pidx

        return from_idx, nidx

    valid_prev = valid_next = valid
    _group(tlist, sql.Operation, match,
           valid_prev, valid_next, post, extend=False)


def group_identifier_list(tlist):
    m_role = [(T.Keyword, ('null', 'role')), (T.Punctuation, ',')]
    sqlcls = (sql.Function, sql.WindowFunction, sql.Case, sql.Identifier, sql.Comparison,
              sql.IdentifierList, sql.Operation, sql.Comment, sql.SubQuery)
    ttypes = (T_NUMERICAL + T_STRING + T_NAME
              + (T.Keyword, T.Wildcard))
              # + T.Wildcard)

    def match(token):
        # return token.match(T.Punctuation, ',') or isinstance(token, (sql.Comment, sql.Identifier, sql.SubQuery))
        return token.match(T.Punctuation, ',')

    def valid(token):
        return imt(token, i=sqlcls, m=m_role, t=ttypes)

    def post(tlist, pidx, tidx, nidx):
        from_idx, to_idx = pidx, nidx
        if isinstance(tlist.tokens[from_idx], sql.Comment):
            from_idx, _ = tlist.token_next_by(idx=from_idx, i=sqlcls, m=m_role, t=ttypes, reverse=True)
        if isinstance(tlist.tokens[to_idx], sql.Comment):
            to_idx, _ = tlist.token_next_by(idx=to_idx, i=sqlcls, m=m_role, t=ttypes)
        return from_idx, to_idx

    valid_prev = valid_next = valid
    _group(tlist, sql.IdentifierList, match,
           valid_prev, valid_next, post, extend=True)


@recurse(sql.Comment)
def group_comments(tlist):
    tidx, token = tlist.token_next_by(t=T.Comment)
    while token:
        eidx, end = tlist.token_not_matching(
            lambda tk: imt(tk, t=T.Comment) or tk.is_whitespace, idx=tidx)
        if end is not None:
            eidx, end = tlist.token_prev(eidx, skip_ws=False)
            tlist.group_tokens(sql.Comment, tidx, eidx)

        tidx, token = tlist.token_next_by(t=T.Comment, idx=tidx)


@recurse(sql.ClauseWhere)
def group_clause_where(tlist):

    tidx, token = tlist.token_next_by(m=sql.ClauseWhere.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(m=sql.ClauseWhere.M_CLOSE, i=sql.ClauseWhere.I_CLOSE, idx=tidx)

        if end is None:
            end = tlist._groupable_tokens[-1]
        else:
            end = tlist.tokens[eidx - 1]
        # TODO: convert this to eidx instead of end token.
        # i think above values are len(tlist) and eidx-1
        eidx = tlist.token_index(end)
        tlist.group_tokens(sql.ClauseWhere, tidx, eidx)
        tidx, token = tlist.token_next_by(m=sql.ClauseWhere.M_OPEN, idx=tidx)


@recurse()
def group_aliased(tlist):
    I_ALIAS = (sql.Function, sql.Case, sql.Identifier,
               sql.Operation, sql.Comparison, sql.WindowFunction)

    tidx, token = tlist.token_next_by(i=I_ALIAS, t=T.Number)
    while token:
        nidx, next_ = tlist.token_next(tidx)
        if isinstance(next_, sql.Identifier):
            tlist.group_tokens(sql.Identifier, tidx, nidx, extend=True)
        tidx, token = tlist.token_next_by(i=I_ALIAS, t=T.Number, idx=tidx)


@recurse()
def group_sub_query(tlist):
    I_ALIAS = (sql.Parenthesis, sql.Identifier)

    tidx, token = tlist.token_next_by(i=I_ALIAS)
    while token:
        nidx, next_ = tlist.token_next(idx=tidx)
        if next_ and next_.match(T.Keyword, 'AS', False):
            nidx, next_ = tlist.token_next(nidx)
        if isinstance(token, sql.Parenthesis) and isinstance(next_, sql.Identifier):
            if token.is_subQuery:
                tlist.group_tokens(sql.SubQuery, tidx, nidx, extend=True)
        elif isinstance(token, sql.Identifier) and isinstance(next_, sql.Parenthesis):
            if next_.is_subQuery:
                tlist.group_tokens(sql.SubQuery, tidx, nidx, extend=True)
        tidx, token = tlist.token_next_by(idx=tidx, i=I_ALIAS)


@recurse(sql.ClauseFrom)
def group_clause_from(tlist):
    tidx, token = tlist.token_next_by(m=sql.ClauseFrom.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(m=sql.ClauseFrom.M_CLOSE, i=sql.ClauseFrom.I_CLOSE, idx=tidx)

        if end is None:
            end = tlist._groupable_tokens[-1]
        else:
            end = tlist.tokens[eidx - 1]
        # TODO: convert this to eidx instead of end token.
        # i think above values are len(tlist) and eidx-1
        eidx = tlist.token_index(end)
        tlist.group_tokens(sql.ClauseFrom, tidx, eidx)
        tidx, token = tlist.token_next_by(m=sql.ClauseFrom.M_OPEN, idx=tidx)


@recurse(sql.SelectProjection)
def group_select_projection(tlist):
        tidx, token = tlist.token_next_by(m=sql.SelectProjection.M_OPEN)
        while token:
            eidx, end = tlist.token_next_by(i=(sql.IdentifierList, sql.Identifier), t=(T.Wildcard), idx=tidx)
            if end is None:
                raise SQLParseError('Invalid syntax for SELECT clause: Identifiers missing.')
            else:
                tlist.group_tokens(sql.SelectProjection, tidx, eidx)

            tidx, token = tlist.token_next_by(m=sql.SelectProjection.M_OPEN, idx=tidx)


@recurse(sql.ClauseWith)
def group_clause_with(tlist):
    tidx, token = tlist.token_next_by(m=sql.ClauseWith.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(i=(sql.IdentifierList, sql.Identifier, sql.SubQuery), idx=tidx)

        if end is None:
            raise SQLParseError('Invalid syntax for WITH clause: Identifiers missing.')
        else:
            tlist.group_tokens(sql.ClauseWith, tidx, eidx)

        tidx, token = tlist.token_next_by(m=sql.ClauseWith.M_OPEN, idx=tidx)


@recurse(sql.ClausePartitionBy)
def group_clause_partition_by(tlist):

    tidx, token = tlist.token_next_by(m=sql.ClausePartitionBy.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(i=(sql.Identifier, sql.IdentifierList), idx=tidx)

        eidx = tlist.token_index(end)
        tlist.group_tokens(sql.ClausePartitionBy, tidx, eidx)
        tidx, token = tlist.token_next_by(m=sql.ClausePartitionBy.M_OPEN, idx=tidx)


@recurse(sql.ClauseOrderBy)
def group_clause_order_by(tlist):

    tidx, token = tlist.token_next_by(m=sql.ClauseOrderBy.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(i=(sql.Identifier, sql.IdentifierList, sql.Function), idx=tidx)

        eidx = tlist.token_index(end)
        tlist.group_tokens(sql.ClauseOrderBy, tidx, eidx)
        tidx, token = tlist.token_next_by(m=sql.ClauseOrderBy.M_OPEN, idx=tidx)


@recurse(sql.ClauseGroupBy)
def group_clause_group_by(tlist):

    tidx, token = tlist.token_next_by(m=sql.ClauseGroupBy.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(i=(sql.Identifier, sql.IdentifierList), idx=tidx)

        eidx = tlist.token_index(end)
        tlist.group_tokens(sql.ClauseGroupBy, tidx, eidx)
        tidx, token = tlist.token_next_by(m=sql.ClauseGroupBy.M_OPEN, idx=tidx)


@recurse(sql.ClauseInsert)
def group_clause_insert(tlist):
    tidx, token = tlist.token_next_by(m=sql.ClauseInsert.M_OPEN)
    while token:
        eidx, end = tlist.token_next_by(i=sql.Parenthesis, idx=tidx)

        if end is None:
            raise SQLParseError('Invalid syntax for INSERT clause: Identifiers missing.')
        else:
            tlist.group_tokens(sql.ClauseInsert, tidx, eidx)

        tidx, token = tlist.token_next_by(m=sql.ClauseInsert.M_OPEN, idx=tidx)


@recurse(sql.StatementSelect)
def group_statement_select(tlist):

    if isinstance(tlist, sql.Parenthesis) or tlist.tokens[-1].match(T.Punctuation, ')', False):
        offset = 1
    else:
        offset = 0

    tidx, token = tlist.token_next_by(i=sql.StatementSelect.I_OPEN)
    while token:
        sidx = tidx

        prev_tidx, prev_token = tlist.token_next(idx=tidx, reverse=True)
        if isinstance(prev_token, sql.ClauseWith):
            sidx = prev_tidx

        eidx, end = tlist.token_next_by(m=sql.StatementSelect.M_CLOSE, idx=tidx)

        if end is None:
            eidx = len(tlist.tokens) - 1 - offset
        else:
            eidx-=1

        tlist.group_tokens(sql.StatementSelect, sidx, eidx)
        tidx, token = tlist.token_next_by(i=sql.StatementSelect.I_OPEN, idx=tidx)


def group_statement_union(tlist):

    sqlcls = (sql.StatementSelect, sql.StatementUnion, sql.Comment)

    idsep_mpatt = (T.Keyword, ('UNION', 'UNION ALL'), True)

    def match(token):
        return imt(token, m=idsep_mpatt)

    def valid(token):
        if imt(token, i=sqlcls):
            return True
        return False

    def post(tlist, pidx, tidx, nidx):
        from_idx, to_idx = pidx, nidx
        if isinstance(tlist.tokens[from_idx], sql.Comment):
            from_idx, _ = tlist.token_next_by(idx=from_idx, i=sqlcls, reverse=True)
        if isinstance(tlist.tokens[to_idx], sql.Comment):
            to_idx, _ = tlist.token_next_by(idx=to_idx, i=sqlcls)
        return from_idx, to_idx

    valid_prev = valid_next = valid
    _group(tlist, sql.StatementUnion, match,
           valid_prev, valid_next, post, extend=True)

@recurse(sql.StatementInsert)
def group_statement_insert(tlist):
    # tidx, token = tlist.token_next_by(m=sql.StatementInsert.M_OPEN)
    tidx, token = tlist.token_next_by(i=sql.StatementInsert.I_OPEN)
    while token:
        sidx = tidx

        prev_tidx, prev_token = tlist.token_next(idx=tidx, reverse=True)
        if isinstance(prev_token, sql.ClauseWith):
            sidx = prev_tidx

        eidx, end = tlist.token_next_by(m=sql.StatementInsert.M_CLOSE, idx=tidx)
        eidx -= 1

        tlist.group_tokens(sql.StatementInsert, sidx, eidx)
        # tidx, token = tlist.token_next_by(m=sql.StatementInsert.M_OPEN, idx=tidx)
        tidx, token = tlist.token_next_by(i=sql.StatementInsert.I_OPEN, idx=tidx)


@recurse(sql.Function)
def group_functions(tlist):
    has_create = False
    has_table = False
    for tmp_token in tlist.tokens:
        if tmp_token.value == 'CREATE':
            has_create = True
        if tmp_token.value == 'TABLE':
            has_table = True
    if has_create and has_table:
        return

    tidx, token = tlist.token_next_by(t=(T.Name.Builtin))
    while token:
        nidx, next_ = tlist.token_next(tidx, skip_ws=True)
        if isinstance(next_, sql.Parenthesis):
            tlist.group_tokens(sql.Function, tidx, nidx)
        tidx, token = tlist.token_next_by(t=T.Name, idx=tidx)

def group_window_function(tlist):
    def match(token):
        return token.match(*sql.WindowFunction.M_OPEN)

    def valid_prev(token):
        return imt(token, i=(sql.Function, sql.WindowFunction))

    def valid_next(token):
        return imt(token, i=sql.Parenthesis)

    def post(tlist, pidx, tidx, nidx):
        from_idx = pidx
        to_idx = nidx
        token  = tlist.tokens[tidx]

        if token.match(T.Keyword, 'FILTER'):
            idx_over, t_over = tlist.token_next_by(idx=to_idx, m=[(T.Keyword, ('OVER', 'FROM'), False), (T.Punctuation, ',', False)])
            if t_over and t_over.match(T.Keyword, 'OVER', False):
                to_idx, _ = tlist.token_next_by(idx=idx_over, i=sql.Parenthesis)

        return from_idx, to_idx

    _group(tlist, sql.WindowFunction, match,
           valid_prev, valid_next, post, extend=True)


def group_order(tlist):
    """Group together Identifier and Asc/Desc token"""
    tidx, token = tlist.token_next_by(t=T.Keyword.Order)
    while token:
        pidx, prev_ = tlist.token_prev(tidx)
        if imt(prev_, i=sql.Identifier, t=T.Number):
            tlist.group_tokens(sql.Identifier, pidx, tidx)
            tidx = pidx
        tidx, token = tlist.token_next_by(t=T.Keyword.Order, idx=tidx)


@recurse()
def align_comments(tlist):
    tidx, token = tlist.token_next_by(i=sql.Comment)
    while token:
        # pidx, prev_ = tlist.token_prev(tidx)
        # if isinstance(prev_, sql.TokenList):
        #     tlist.group_tokens(sql.TokenList, pidx, tidx, extend=True)
        #     tidx = pidx
        tidx, token = tlist.token_next_by(i=sql.Comment, idx=tidx)


def group_values(tlist):
    tidx, token = tlist.token_next_by(m=(T.Keyword, 'VALUES'))
    start_idx = tidx
    end_idx = -1
    while token:
        if isinstance(token, sql.Parenthesis):
            end_idx = tidx
        tidx, token = tlist.token_next(tidx)
    if end_idx != -1:
        tlist.group_tokens(sql.Values, start_idx, end_idx, extend=True)


def group(stmt):

    group_comments(stmt)
    group_brackets(stmt)
    group_parenthesis(stmt)
    group_case(stmt)
    group_if(stmt)
    group_for(stmt)
    group_begin(stmt)
    group_functions(stmt)
    group_window_function(stmt)
    group_period(stmt)
    group_arrays(stmt)
    group_identifier(stmt)
    group_signed_identifier(stmt)
    group_order(stmt)
    group_typecasts(stmt)
    group_tzcasts(stmt)
    group_typed_literal(stmt)
    group_operator(stmt)
    group_comparison(stmt)
    group_as(stmt)
    group_sub_query(stmt)
    group_aliased(stmt)
    group_assignment(stmt)
    group_conditions_list(stmt)
    align_comments(stmt)
    group_identifier_list(stmt)
    group_clause_partition_by(stmt)
    group_clause_order_by(stmt)
    group_clause_group_by(stmt)
    group_values(stmt)
    group_clause_where(stmt)
    group_clause_from(stmt)
    group_select_projection(stmt)
    group_clause_with(stmt)
    group_clause_insert(stmt)
    group_statement_select(stmt)
    group_statement_union(stmt)
    group_statement_insert(stmt)

    return stmt


def _group(tlist, cls, match,
           valid_prev=lambda t: True,
           valid_next=lambda t: True,
           post=None,
           extend=True,
           recurse=True
           ):
    """Groups together tokens that are joined by a middle token. i.e. x < y"""

    tidx_offset = 0
    pidx, prev_ = None, None
    iterable = enumerate(list(tlist))
    for idx, token in iterable:
        tidx = idx - tidx_offset
        if tidx < 0:  # tidx shouldn't get negative
           continue

        if token.is_whitespace:
            continue

        if recurse and token.is_group and not isinstance(token, cls):
            _group(token, cls, match, valid_prev, valid_next, post, extend)

        if match(token):
            nidx, next_ = tlist.token_next(tidx)
            if prev_ and valid_prev(prev_) and valid_next(next_):
                from_idx, to_idx = post(tlist, pidx, tidx, nidx)
                if from_idx:
                    for indx in range(tidx+1, to_idx+1):
                        if recurse and tlist.tokens[indx].is_group and not isinstance(tlist.tokens[indx], cls):
                            _group(tlist.tokens[indx], cls, match, valid_prev, valid_next, post, extend)

                    try:
                        grp = tlist.group_tokens(cls, from_idx, to_idx, extend=extend)
                        tidx_offset += to_idx - from_idx
                        consume(iterable, to_idx - tidx)  # consumes to_idx-tidx characters
                        pidx, prev_ = from_idx, grp
                        continue
                    except Exception as e:
                        print("{0}: {1} grouping \n{2}".format(str(e.__class__), e, tlist.value))

        pidx, prev_ = tidx, token

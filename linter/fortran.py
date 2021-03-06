""" A Fortran code analyzer and linter. """
from argparse import ArgumentParser
from collections import defaultdict, namedtuple

from . import alphanumeric, letter, digit, one_of, whitespace, none_of
from . import Failure, succeed, matches, spaces, wildcard
from . import join, exact, liberal, satisfies, singleton, EOF, parser, concat


def inexact(string):
    """ Ignore case. """
    return exact(string, ignore_case=True)


def keyword(string):
    """ Match a case-insensitive keyword. """
    return liberal(inexact(string))


def sum_parsers(parsers):
    """ Construct a sequential parser from a list of parsers. """
    result = succeed("")

    for this in parsers:
        result = result + this

    return result


class Token(object):
    """ Classification of tokens. """
    def __init__(self, tag, value):
        self.value = value
        self.tag = tag

    def __repr__(self):
        return self.tag + "{" + self.value + "}"


def tag_token(tag):
    """
    Returns a function that wraps a value with
    the specified `tag`.
    """
    def inner(value):
        """ The function that applies the `tag`. """
        return Token(tag, value)
    return inner


def name_tokens(list_of_tokens):
    """ Only select the tokens that have the tag 'name'. """
    return [token.value.lower()
            for token in list_of_tokens
            if token.tag == 'name']


class Grammar(object):
    """ Grammar specification of Fortran 77. """
    #: position of column where the continuation mark should be
    continuation_column = 5

    #: starting column for code
    margin_column = continuation_column + 1

    #: classification of statements
    statements = {}
    statements["control nonblock"] = [['go', 'to'], ['call'], ['return'],
                                      ['continue'], ['stop'], ['pause']]

    statements["control block"] = [['if'], ['else', 'if'],
                                   ['else'], ['end', 'if'], ['do'],
                                   ['end', 'do']]

    statements["control"] = (statements["control block"] +
                             statements["control nonblock"])

    statements["io"] = [['read'], ['write'], ['print'], ['rewind'],
                        ['backspace'], ['endfile'], ['open'], ['close'],
                        ['inquire']]

    statements["assign"] = [['assign']]

    statements["executable"] = (statements["control"] +
                                statements["assign"] + statements["io"])

    statements["type"] = [['integer'], ['real'], ['double', 'precision'],
                          ['complex'], ['logical'], ['character']]

    statements["specification"] = (statements["type"] +
                                   [['dimension'], ['common'],
                                    ['equivalence'], ['implicit'],
                                    ['parameter'], ['external'],
                                    ['intrinsic'], ['save']])

    statements["top level"] = [['program'], ['end', 'program'], ['function'],
                               ['end', 'function'], ['subroutine'],
                               ['end', 'subroutine'], ['block', 'data'],
                               ['end', 'block', 'data'], ['end']]

    statements["misc nonexec"] = [['entry'], ['data'], ['format']]

    statements["non-executable"] = (statements["specification"] +
                                    statements["misc nonexec"] +
                                    statements["top level"])

    # order is important here
    # because 'end' should come before 'end if' et cetera
    statements["all"] = (statements["executable"] +
                         statements["non-executable"])

    #: intrinsic functions
    intrinsics = ['abs', 'acos', 'aimag', 'aint', 'alog',
                  'alog10', 'amax10', 'amax0', 'amax1', 'amin0',
                  'amin1', 'amod', 'anint', 'asin', 'atan',
                  'atan2', 'cabs', 'ccos', 'char', 'clog',
                  'cmplx', 'conjg', 'cos', 'cosh', 'csin',
                  'csqrt', 'dabs', 'dacos', 'dasin', 'datan',
                  'datan2', 'dble', 'dcos', 'dcosh', 'ddim',
                  'dexp', 'dim', 'dint', 'dint', 'dlog', 'dlog10',
                  'dmax1', 'dmin1', 'dmod', 'dnint', 'dprod',
                  'dreal', 'dsign', 'dsin', 'dsinh', 'dsqrt',
                  'dtan', 'dtanh', 'exp', 'float', 'iabs', 'ichar',
                  'idim', 'idint', 'idnint', 'iflx', 'index',
                  'int', 'isign', 'len', 'lge', 'lgt', 'lle',
                  'llt', 'log', 'log10', 'max', 'max0', 'max1',
                  'min', 'min0', 'min1', 'mod', 'nint', 'real',
                  'sign', 'sin', 'sinh', 'sngl', 'sqrt', 'tan', 'tanh',
                  'matmul', 'cycle']

    #: one word
    term = inexact

    #: valid Fortran identifier
    name = letter + alphanumeric.many() // join
    #: statement label
    label = digit.between(1, 5) // join
    #: integer literal
    integer = (one_of("+-").optional() + +digit) // join
    #: logical literal
    logical = term(".true.") | term(".false.")
    #: character literal segment
    char_segment = ((term('"') + none_of('"').many() // join + term('"')) |
                    (term("'") + none_of("'").many() // join + term("'")))

    #: character literal (string)
    character = (+char_segment) // join
    #: basic real number
    basic_real = (one_of("+-").optional() + +digit +
                  exact(".") // singleton + digit.many()) // join
    #: single precision exponent part
    single_exponent = one_of("eE") + integer
    #: single precision real
    single = ((basic_real + single_exponent.optional() // join) |
              (integer + single_exponent))
    #: double precision exponent part
    double_exponent = one_of("dD") + integer
    #: double precision real
    double = (basic_real | integer) + double_exponent
    #: real number literal
    real = double | single
    #: comment line
    comment = exact("!") + none_of("\n").many() // join
    #: arithmetic operators
    equals, plus, minus, times, slash = [exact(c) for c in "=+-*/"]
    #: comparison operators
    lt_, le_, eq_, ne_, gt_, ge_ = [term(c)
                                    for c in ['.lt.', '.le.', '.eq.',
                                              '.ne.', '.gt.', '.ge.']]
    #: logical operators
    not_, and_, or_ = [term(c)
                       for c in ['.not.', '.and.', '.or.']]
    #: more logical operators
    eqv, neqv = [term(c) for c in ['.eqv.', '.neqv.']]
    lparen, rparen, dot, comma, dollar = [exact(c) for c in "().,$"]
    apostrophe, quote, colon, langle, rangle = [exact(c) for c in "'\":<>"]
    #: exponentiation operator
    exponent = exact("**")
    #: string concatenation operator
    concatenation = exact("//")

    #: one single token
    single_token = (character // tag_token("character") |
                    comment // tag_token("comment") |
                    logical // tag_token("logical") |
                    lt_ // tag_token("lt") |
                    le_ // tag_token("le") |
                    eq_ // tag_token("eq") |
                    ne_ // tag_token("ne") |
                    gt_ // tag_token("gt") |
                    ge_ // tag_token("ge") |
                    not_ // tag_token("not") |
                    and_ // tag_token("and") |
                    or_ // tag_token("or") |
                    eqv // tag_token("eqv") |
                    neqv // tag_token("neqv") |
                    real // tag_token("real") |
                    integer // tag_token("integer") |
                    name // tag_token("name") |
                    equals // tag_token("equals") |
                    plus // tag_token("plus") |
                    minus // tag_token("minus") |
                    exponent // tag_token("exponent") |
                    times // tag_token("times") |
                    concatenation // tag_token("concat") |
                    slash // tag_token("slash") |
                    lparen // tag_token("lparen") |
                    rparen // tag_token("rparen") |
                    dot // tag_token("dot") |
                    comma // tag_token("comma") |
                    dollar // tag_token("dollar") |
                    apostrophe // tag_token("apostrophe") |
                    quote // tag_token("quote") |
                    colon // tag_token("colon") |
                    langle // tag_token("langle") |
                    rangle // tag_token("rangle") |
                    spaces // tag_token("whitespace") |
                    wildcard // tag_token("unknown"))

    #: list of tokens
    tokenizer = (single_token).many()


def outer_block(statement):
    """ Returns a function that marks a block with `statement`. """
    def inner(children):
        """
        Wraps `children` in an :class:`OuterBlock`
        marked as `statement`.
        """
        return OuterBlock(children, statement)
    return inner


class OuterBlock(object):
    """ Represents a block. Its children are inner blocks. """
    def __init__(self, children, statement):
        self.children = children
        self.statement = statement

    def accept(self, visitor):
        """
        Accept a visitor by invoking its outer block processing function.
        """
        return visitor.outer_block(self)

    def __repr__(self):
        return print_details(self)

    def __str__(self):
        return plain(self)


def inner_block(logical_lines):
    """ Wraps a collection of logical lines into an :class:`InnerBlock`. """
    return InnerBlock(logical_lines)


class InnerBlock(object):
    """ Represents the statements inside a block. """
    def __init__(self, logical_lines):
        statements = Grammar.statements

        @parser
        def if_block(text, start):
            """ Process an ``if`` block or statement. """
            def new_style_if(list_of_lines):
                """ An ``if`` statement accompanied by a ``then`` keyword. """
                then = [token
                        for token in name_tokens(list_of_lines.tokens_after)
                        if token == 'then']
                return len(then) > 0

            if_statement = one_of_types([["if"]])
            else_if_statement = one_of_types([["else", "if"]])
            else_statement = one_of_types([["else"]])
            end_if_statement = one_of_types([["end", "if"]])

            begin = (if_statement.guard(new_style_if, "new style if") //
                     singleton)
            inner = (non_block | do_block | if_block |
                     none_of_types([["end", "if"], ["else", "if"], ["else"]]))
            else_or_else_if = else_if_statement | else_statement

            def inner_block_or_empty(list_of_lines):
                """
                Wraps a list of lines in an :class:`InnerBlock`
                if not already empty.
                """
                if list_of_lines != []:
                    return [inner_block(list_of_lines)]
                else:
                    return []

            section = ((inner.many() // inner_block_or_empty) +
                       else_or_else_if.optional()).guard(lambda l: l != [],
                                                         "anything")
            sections = section.many() // concat

            end = (end_if_statement // singleton)

            result = (((begin + sections + end) // outer_block("if_block"))
                      .scan(text, start))

            return result

        @parser
        def do_block(text, start):
            """ Process a ``do`` block. """
            def new_style_do(list_of_lines):
                """ A proper ``do`` block with ``end do``. """
                return not matches(keyword("do") + liberal(Grammar.label),
                                   list_of_lines.code.lower())

            do_statement = one_of_types([["do"]])
            end_do_statement = one_of_types([["end", "do"]])

            begin = (do_statement.guard(new_style_do, "new style do") //
                     singleton)

            inner = ((non_block | do_block | if_block |
                      none_of_types([["end", "do"]]))
                     .many() // inner_block // singleton)
            end = end_do_statement // singleton

            return (((begin + inner + end) // outer_block("do_block"))
                    .scan(text, start))

        non_block = one_of_types(statements["io"] + statements["assign"] +
                                 statements["specification"] +
                                 statements["misc nonexec"] +
                                 statements["control nonblock"])

        block_or_line = non_block | do_block | if_block | wildcard

        self.children = block_or_line.many().parse(logical_lines)

    def accept(self, visitor):
        """
        Accept a visitor by invoking its inner block processing function.
        """
        return visitor.inner_block(self)

    def __repr__(self):
        return print_details(self)

    def __str__(self):
        return plain(self)


class RawLine(object):
    """
    Represents a line in the source code.
    Classifies whether the line is a comment,
    an initial or a continuation line.
    """
    def __init__(self, line):
        self.original = line

        continuation_column = Grammar.continuation_column
        margin_column = Grammar.margin_column

        lowered = line.rstrip().lower()

        if matches(EOF | one_of("*c") | keyword("!"), lowered):
            self.type = "comment"
            return

        self.code = line[margin_column:]
        self.tokens = Grammar.tokenizer.parse(self.code)
        self.tokens_after = self.tokens

        if len(lowered) > continuation_column:
            if matches(none_of("0 "), lowered, continuation_column):
                self.type = "continuation"
                assert len(lowered[:continuation_column].strip()) == 0
                self.cont = line[continuation_column:margin_column]
                return

        self.type = "initial"

        # extract the statement label if applicable
        statement_label = lowered[:continuation_column]
        if len(statement_label.strip()) > 0:
            self.label = (liberal(Grammar.label) // int).parse(statement_label)

        def check(words):
            """ See if the words match any known (sequence of) keywords. """
            msg = succeed(" ".join(words))
            parser_sum = sum_parsers([keyword(w) for w in words])

            try:
                success = (parser_sum >> msg).scan(self.code)
                tokenizer = Grammar.tokenizer

                self.statement = success.value
                self.tokens_after = tokenizer.parse(self.code,
                                                    success.end)

                # seems like a have a complete match
                raise StopIteration()
            except Failure:
                pass

        try:
            for words in Grammar.statements["all"]:
                check(words)
        except StopIteration:
            return

        self.statement = 'assignment'

    def accept(self, visitor):
        """
        Accept a visitor by invoking its raw line processing function.
        """
        return visitor.raw_line(self)

    def __repr__(self):
        return print_details(self)

    def __str__(self):
        return plain(self)


class LogicalLine(object):
    """ Represents a logical line. Continuation lines are merged. """
    def __init__(self, children):
        initial_line = [l for l in children if l.type == 'initial']
        assert len(initial_line) == 1
        initial_line = initial_line[0]

        self.children = children
        self.statement = initial_line.statement

        try:
            self.label = initial_line.label
        except AttributeError:
            pass

        code_lines = [l for l in children if l.type != 'comment']

        self.code = "\n".join([l.code for l in code_lines])
        self.tokens = concat([l.tokens for l in code_lines])
        self.tokens_after = concat([l.tokens_after for l in code_lines])

    def accept(self, visitor):
        """
        Accept a visitor by invoking its logical line processing function.
        """
        return visitor.logical_line(self)

    def __repr__(self):
        return print_details(self)

    def __str__(self):
        return plain(self)


def parse_into_logical_lines(lines):
    """ Groups a set of raw lines into logical lines. """
    def of_type(type_name):
        """ A parser that recognizes only a specific kind of raw line. """
        return satisfies(lambda l: l.type == type_name, type_name)

    comment, continuation, initial = (of_type(t)
                                      for t in ['comment',
                                                'continuation', 'initial'])

    logical_line = (comment.many() + initial // singleton +
                    (comment | continuation).many()) // LogicalLine

    return logical_line.many().parse(lines)


def parse_source(logical_lines):
    """ Organizes a list of logical lines into blocks. """
    statements = Grammar.statements

    def top_level_block(kind, first_line_optional=False):
        """
        Parses a top level block: the main program,
        a function, a subroutine or a block data.
        """
        if first_line_optional:
            first_line = one_of_types([kind]).optional()
        else:
            first_line = one_of_types([kind]) // singleton

        mid_lines = (none_of_types(statements["top level"]).many() //
                     inner_block // singleton)
        last_line = one_of_types([["end"] + kind, ["end"]]) // singleton

        block_statement = "_".join(kind + ["block"])

        return ((first_line + mid_lines + last_line) //
                outer_block(block_statement))

    function, subroutine, block_data = [top_level_block(kind)
                                        for kind in [["function"],
                                                     ["subroutine"],
                                                     ["block", "data"]]]

    subprogram = function | subroutine | block_data

    main_program = top_level_block(["program"], True)

    program_unit = subprogram | main_program

    return (+program_unit // outer_block("source_file")).parse(logical_lines)


def one_of_list(names):
    """ Readable representation of a list of alternatives. """
    if len(names) == 0:
        return "nothing"
    if len(names) == 1:
        return " ".join(names[0])
    if len(names) == 2:
        return " ".join(names[0]) + " or " + " ".join(names[1])

    proper_names = [" ".join(name) for name in names]
    return "one of " + ", ".join(proper_names) + " or " + " ".join(names[-1])


def one_of_types(names):
    """ Whether the statement belongs to any one of the given types. """
    return satisfies(lambda l: l.statement in [" ".join(name)
                                               for name in names],
                     one_of_list(names))


def none_of_types(names):
    """ Whether the statement belongs to none of the given types. """
    return satisfies(lambda l: l.statement not in [" ".join(name)
                                                   for name in names],
                     one_of_list(names))


def remove_blanks(raw_lines):
    """ Removes empty lines from a list of :class:`RawLine` objects. """
    empty = satisfies(lambda l: matches(whitespace << EOF, l.original),
                      "empty line")
    remove = (+empty // (lambda ls: RawLine("\n")) | wildcard).many()
    return str((remove // outer_block("source")).parse(raw_lines))


def new_comments(raw_lines):
    """ Converts old style comments to new style ones. """
    def of_type(type_name):
        """ Whether the line is of some particular type. """
        return satisfies(lambda l: l.type == type_name, type_name)

    def change_comment(line):
        """ Replace old comment characters with '!'. """
        if matches(one_of("c*"), line.original):
            return RawLine("!" + line.original[1:])
        else:
            return line

    upgrade = of_type("comment") // change_comment | wildcard
    return str((upgrade.many() // outer_block("source")).parse(raw_lines))


class Visitor(object):
    """
    Template for implementors of the visitor pattern.
    The default implementation just returns the original source code.
    """
    def raw_line(self, line):
        """ Process a raw line. """
        return [line.original]

    def logical_line(self, line):
        """ Process a logical line with continuations taken into account. """
        return concat([l.accept(self) for l in line.children])

    def inner_block(self, block):
        """ Process the inside lines of a block. """
        return concat([b.accept(self) for b in block.children])

    def outer_block(self, block):
        """ Process lines including the bracketing ones for a block. """
        return concat([b.accept(self) for b in block.children])

    def top_level(self, block):
        """ Process the top most level of a source file. """
        return "".join(block.accept(self))


def indent(doc, indent_width=4):
    """ Re-indent source code. """
    margin_column = Grammar.margin_column

    class Indent(Visitor):
        """ Visitor implementation of re-indentation. """
        def __init__(self):
            # current level of indentation
            self.current = 1

        def raw_line(self, line):
            if line.type == 'comment':
                return [line.original]

            if line.type == 'continuation':
                tab = " " * (self.current + indent_width)
            else:
                tab = " " * self.current

            return [line.original[:margin_column] + tab + line.code.lstrip()]

        def inner_block(self, block):
            self.current += indent_width
            result = concat([b.accept(self) for b in block.children])
            self.current -= indent_width
            return result

    return Indent().top_level(doc)


def plain(doc):
    """ Basically no processing, just return the source code intact. """
    return Visitor().top_level(doc)


def remove_comments(doc):
    """ Remove comments from source code. """
    class Remove(Visitor):
        """ Visitor implementation of comment removal. """
        def raw_line(self, line):
            if line.type == 'comment':
                return []
            else:
                return [line.original]

    return Remove().top_level(doc)


def print_details(doc):
    """ Print details of the parse tree for easy inspection. """
    class Details(Visitor):
        """ Visitor implementation of details. """
        def __init__(self):
            self.level = 0
            self.statement = None

        def raw_line(self, line):
            if line.type == "comment":
                return []

            elif line.type == "continuation":
                self.level += 1
                result = ["||| " * self.level + self.statement +
                          " continued: " + line.code.lstrip()]
                self.level -= 1
                return result

            elif line.type == "initial":
                try:
                    info = "{}[{}]: ".format(line.statement, line.label)
                except AttributeError:
                    info = "{}: ".format(line.statement)

                return ["||| " * self.level + info + line.code.lstrip()]

        def logical_line(self, line):
            self.statement = line.statement
            return concat([b.accept(self) for b in line.children])

        def inner_block(self, block):
            self.level += 1
            result = concat([b.accept(self) for b in block.children])
            self.level -= 1
            return result

    return Details().top_level(doc)


def read_file(filename):
    """
    Read the contents of a file and convert it to a list of :class:`RawLine`
    objects.
    """
    with open(filename) as input_file:
        return [RawLine(line) for line in input_file]


def parse_file(filename):
    """
    Read the contents of a file and convert it to our internal
    representation of nested blocks.
    """
    return parse_source(parse_into_logical_lines(read_file(filename)))


def reconstruct(unit):
    """
    Re-construct the source code from parsed representation.
    """
    class Reconstruct(Visitor):
        """ Visitor implementation of the reconstruction. """
        def raw_line(self, line):
            if line.type == 'comment':
                return [line.original]

            cont_col = Grammar.continuation_column
            marg_col = Grammar.margin_column

            if line.type == 'continuation':
                result = " " * cont_col + line.cont
            else:
                try:
                    result = ("{:<" + str(marg_col) + "}").format(line.label)
                except AttributeError:
                    result = " " * marg_col

            for token in line.tokens:
                result += token.value
            return [result]

    return Reconstruct().top_level(unit)


def collect_unit_names(source):
    """ Return a collection of the defined names in the source code. """
    unit_names = []

    for unit in source.children:
        assert isinstance(unit, OuterBlock)

        first = unit.children[0]

        if isinstance(first, LogicalLine):
            unit_names.append(mentioned_names(first)[0])

    return unit_names


def analyze(source):
    """
    Analyze the source code and spit out detailed information about it.
    """
    unit_names = collect_unit_names(source)

    print 'line numbers refer to the line number within the program unit'
    print 'not counting blank lines'
    print
    print 'found program units:', unit_names
    print

    for unit in source.children:
        analyze_unit(unit, unit_names)


def mentioned_names(line):
    """ The defined names that have been actually used in the program. """
    return [token for token in name_tokens(line.tokens_after)]


def analyze_header(unit):
    """
    Extract information about the formal parameters
    of a top-level block.
    """
    first = unit.children[0]

    if isinstance(first, LogicalLine):
        statement = first.statement

        tokens = [token for token in first.tokens_after
                  if token.tag != 'whitespace' and token.tag != 'comment']

        assert len(tokens) > 0
        assert tokens[0].tag == 'name', "got {}".format(tokens[0].tag)

        program_name = tokens[0].value
        formal_params = name_tokens(tokens[1:])

        assert len(unit.children) == 3
        main_block = unit.children[1]

    else:
        statement = "program"
        program_name = None
        formal_params = []

        assert len(unit.children) == 2
        main_block = unit.children[0]

    return statement, program_name, formal_params, main_block

Interval = namedtuple('Interval', ['var', 'start', 'end'])


def make_timeline(occur_dict):
    """
    Create a timeline from the occurrence information for the variables.
    """
    occur_list = [Interval(var, occur_dict[var][0], occur_dict[var][-1])
                  for var in occur_dict if occur_dict[var] != []]
    return sorted(occur_list, key=lambda x: x.start)


def draw_timeline(occur_list, last_line, graph_cols=60):
    """
    ASCII rendering of timeline information.
    """
    def graph_pos(lineno):
        """ Where in the timeline `lineno` should be. """
        return int(round((float(lineno) / last_line) * graph_cols))

    graph_list = [Interval(d.var, graph_pos(d.start), graph_pos(d.end))
                  for d in occur_list]

    for period in graph_list:
        print "{:10s}|{}{}{}|".format(str(period.var),
                                      " " * period.start,
                                      "=" * (period.end - period.start + 1),
                                      " " * (graph_cols - period.end))

    print


def analyze_labels(main_block):
    """ Analyze label information. """
    class Label(Visitor):
        """ Visitor implemention of collection of labels. """
        def __init__(self):
            self.current_line = 0

        def logical_line(self, line):
            self.current_line += 1

            try:
                if line.statement != 'format':
                    return [(self.current_line, line.label)]
            except AttributeError:
                pass

            return []

    labels = main_block.accept(Label())
    if labels:
        print "labels:", [lbl for _, lbl in labels]
        print

    occur_dict = defaultdict(list)
    last_line = [0]

    for decl_line, lbl in labels:
        class Occurrences(Visitor):
            """ Visitor implementation of occurrence check for labels. """
            def __init__(self):
                self.current_line = 0

            def logical_line(self, line):
                self.current_line += 1
                last_line[0] = self.current_line

                int_tokens = [int(token.value)
                              for token in line.tokens_after
                              if token.tag == 'integer']
                if lbl in int_tokens:
                    occur_dict[lbl].append(self.current_line)

                return []

        main_block.accept(Occurrences())

    for decl_line, lbl in labels:
        print lbl, 'defined at: ' + str(decl_line),
        print 'occurred at: ', occur_dict[lbl]
        occur_dict[lbl] = sorted(occur_dict[lbl] + [decl_line])
    print

    draw_timeline(make_timeline(occur_dict), last_line[0])


def analyze_variables(unit_names, formal_params, main_block):
    """ Analyze variable usage information. """
    class Variables(Visitor):
        """ Collect mentions of variable names. """
        def logical_line(self, line):
            if line.statement == 'format':
                return []
            return mentioned_names(line)

    unique_names = list(set(main_block.accept(Variables())))

    specs = [" ".join(s)
             for s in Grammar.statements["specification"]]

    class Locals(Visitor):
        """ Collect local variable declarations. """
        def logical_line(self, line):
            if line.statement not in specs:
                return []

            name_list = mentioned_names(line)

            if line.statement == 'implicit' and name_list == ['none']:
                return []

            return name_list

    local_variables = list(set(main_block.accept(Locals())))

    keywords = list(set(concat(Grammar.statements["all"]))) + ['then', 'none']

    local_names = list(set(local_variables + formal_params))

    unaccounted_for = list(set(unique_names) - set(local_names) -
                           set(keywords) - set(Grammar.intrinsics) -
                           set(unit_names))
    if unaccounted_for:
        print 'unaccounted for:', unaccounted_for
        print

    concern = list(set(local_variables + formal_params + unaccounted_for))

    occur_dict = defaultdict(list)

    last_line = [0]

    class Occurrences(Visitor):
        """ Collect occurrence information for variables. """
        def __init__(self, var):
            self.current_line = 0
            self.var = var

        def logical_line(self, line):
            self.current_line += 1
            last_line[0] = self.current_line
            if line.statement not in specs:
                if self.var in name_tokens(line.tokens_after):
                    occur_dict[self.var].append(self.current_line)

            return []

    for var in concern:
        main_block.accept(Occurrences(var))

    never_occur_list = sorted([var
                               for var in concern
                               if occur_dict[var] == []])

    if never_occur_list:
        print 'never occurred:', never_occur_list
        print

    for var in occur_dict:
        print var, 'occurred at: ', occur_dict[var]

    draw_timeline(make_timeline(occur_dict), last_line[0])


def analyze_unit(unit, unit_names):
    """ Analyze a unit for labels and variables. """
    statement, program_name, formal_params, main_block = analyze_header(unit)

    print statement, program_name, formal_params
    print

    analyze_labels(main_block)
    analyze_variables(unit_names, formal_params, main_block)


def _argument_parser_():
    arg_parser = ArgumentParser()
    task_list = ['remove-blanks', 'print-details',
                 'indent', 'new-comments', 'plain', 'analyze',
                 'reconstruct', 'remove-comments']
    arg_parser.add_argument("task", choices=task_list,
                            metavar="task",
                            help="in {}".format(task_list))
    arg_parser.add_argument("filename")
    return arg_parser


def main():
    """
    The main entry point for the executable.
    Performs the task specified. Possible tasks are:

    - ``plain``: echo the source file lines back, basically a no-op

    - ``remove-comments``: remove all comments from source code

    - ``remove-blanks``: remove blank lines from source code

    - ``indent``: re-indent source code

    - ``print-details``: detailed information about the structure

    - ``new-comments``: convert old style comments to new (Fortran 90) style

    - ``reconstruct``: try and reconstruct the source code from the nested
      structure

    - ``analyze``: detailed analysis and linting of the code
    """
    arg_parser = _argument_parser_()
    args = arg_parser.parse_args()

    raw_lines = read_file(args.filename)
    logical_lines = parse_into_logical_lines(read_file(args.filename))
    parsed = parse_source(logical_lines)

    if args.task == 'plain':
        print plain(parsed),
    elif args.task == 'remove-comments':
        print remove_comments(parsed)
    elif args.task == 'remove-blanks':
        print remove_blanks(raw_lines),
    elif args.task == 'indent':
        print indent(parsed),
    elif args.task == 'print-details':
        print print_details(parsed),
    elif args.task == 'new-comments':
        print new_comments(raw_lines),
    elif args.task == 'reconstruct':
        print reconstruct(parsed),
    elif args.task == 'analyze':
        analyze(parsed)
    else:
        raise ValueError("invalid choice: {}".format(args.task))


if __name__ == '__main__':
    main()

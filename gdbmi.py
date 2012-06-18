# -*- coding: utf-8 -*-
# Copyright (c) 2012 Mak Nazečić-Andrlon
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import re
from codecs import getdecoder

__all__ = ["parse"]

def decode_string(string):
    return str(getdecoder("unicode_escape")(string)[0])

mi_scanner = re.Scanner([
    (r"\d+", lambda s, t: ("TOKEN", int(t))),
    ("\n", lambda s, t: ("NL", t)),
    (r"\(gdb\)", lambda s, t: ("PROMPT", t)),
    (r"[a-zA-Z_][a-zA-Z0-9_-]*", lambda s, t: ("STRING", t)),
    (r"\[", lambda s, t: ("LBRACKET", t)),
    (r"\]", lambda s, t: ("RBRACKET", t)),
    (r"{", lambda s, t: ("LBRACE", t)),
    (r"}", lambda s, t: ("RBRACE", t)),
    (r",", lambda s, t: ("COMMA", t)),
    (r"\^", lambda s, t: ("CARET", t)),
    (r"\*", lambda s, t: ("ASTERISK", t)),
    (r"\+", lambda s, t: ("PLUS", t)),
    (r"=", lambda s, t: ("EQUALS", t)),
    (r"~", lambda s, t: ("TILDE", t)),
    (r"@", lambda s, t: ("AT", t)),
    (r"&", lambda s, t: ("AMPERSAND", t)),
    (r"\"(?:[^\"\\]|\\.)*\"", lambda s, t: ("CSTRING", decode_string(t[1:-1])))
])


class MIParser(object):
    """Parser for the GDB/MI grammar specification.

    http://sourceware.org/gdb/current/onlinedocs/gdb/GDB_002fMI-Output-Syntax.html

    """

    def parse(self, tokens, pedantic=False):
        self._tokens = iter(tokens)
        self._token = next(self._tokens)
        self.pedantic = pedantic
        return self._output()

    def _output(self):
        oob_records = []
        while not (self._check("CARET") or self._check("PROMPT")):
            oob_records.append(self._out_of_band_record())

        result_record = None
        if self._check("CARET"):
            result_record = self._result_record()

        if not self.pedantic:
            while not self._check("PROMPT"):
                oob_records.append(self._out_of_band_record())

        self._expect("PROMPT")
        self._expect("NL")

        return (oob_records, result_record)

    def _result_record(self):
        token = self._token_maybe()

        self._expect("CARET")
        result_class = self._result_class()

        results = None
        if self._check("COMMA"):
            results = self._comma_prefixed_results()

        self._expect("NL")

        return token, result_class, results

    def _out_of_band_record(self):
        marker = self._lookahead()[0]
        try:
            return {
                "ASTERISK": self._exec_async_output,
                "PLUS": self._status_async_output,
                "EQUALS": self._notify_async_output,
                "TILDE": self._console_stream_output,
                "AT": self._target_stream_output,
                "AMPERSAND": self._log_stream_output,
            }[marker]()
        except KeyError:
            self.error("expected out-of-band record marker, got '%s'" % marker)

    def _exec_async_output(self):
        return self._async_output("ASTERISK")

    def _status_async_output(self):
        return self._async_output("PLUS")

    def _notify_async_output(self):
        return self._async_output("EQUALS")

    def _console_stream_output(self):
        return self._stream_output("TILDE")

    def _target_stream_output(self):
        return self._stream_output("AT")

    def _log_stream_output(self):
        return self._stream_output("AMPERSAND")

    def _stream_output(self, prefix):
        result = (self._expect(prefix)[1], self._cstring())
        if not self.pedantic:
            self._accept("NL")
        return result

    def _async_output(self, prefix):
        return (self._token_maybe(), self._expect(prefix)[1],
                self._async_class(), self._comma_prefixed_results())

    def _async_class(self):
        return self._string()

    def _result_class(self):
        return self._string()

    def _string(self):
        return self._expect("STRING")[1]

    def _cstring(self):
        return self._expect("CSTRING")[1]

    def _result(self):
        result_name = self._string()
        self._expect("EQUALS")
        return {result_name: self._value()}

    def _token_maybe(self):
        token = self._accept("TOKEN")
        if token:
            return token[1]

    def _value(self):
        try:
            return {
                "LBRACKET": self._list,
                "CSTRING": self._cstring,
                "LBRACE": self._tuple,
            }[self._lookahead()[0]]()
        except KeyError:
            self.error("expected list, tuple or C string")

    def _list(self):
        self._expect("LBRACKET")

        if self._accept("RBRACKET"):
            return []

        if self._check("STRING"):
            return self._list_results()

        return self._list_values()

    def _list_values(self):
        values = [self._value()]
        while self._check("COMMA"):
            self._expect("COMMA")
            values.append(self._value())

        self._expect("RBRACKET")

        return values

    def _list_results(self):
        return [self._result()] + self._comma_prefixed_results("RBRACKET")

    def _tuple(self):
        self._expect("LBRACE")
        if self._accept("RBRACE"):
            return []
        return [self._result()] + self._comma_prefixed_results("RBRACE")

    def _comma_prefixed_results(self, closer="NL"):
        results = []
        while self._check("COMMA"):
            self._expect("COMMA")
            results.append(self._result())
        self._expect(closer)
        return results

    def _advance(self):
        old_token = self._token
        self._token = next(self._tokens, (None, None))
        return old_token

    def _lookahead(self):
        return self._token

    def _check(self, expected):
        return self._lookahead()[0] == expected

    def _accept(self, expected):
        if self._check(expected):
            return self._advance()
        return False

    def _expect(self, expected):
        token = self._accept(expected)
        if not token:
            self.error("expected %s, got %s" % (expected, self._token[0]))
        return token

    def error(self, what):
        raise SyntaxError(what)


def parse(text, pedantic=False):
    """Parse output from GDB/MI and return a nested structure describing it.

    Returns a 2-tuple as ([out-of-band records], result record). Result and
    asynchronous records are given as 4-tuples of (token, sigil, name, data).
    Stream records are given as 2-tuples of (sigil, string). Lists are
    converted to native lists, results and tuples are converted to dicts, and
    strings are converted to native strings.

    """
    tokens, remainder = mi_scanner.scan(text)
    assert not remainder
    return MIParser().parse(tokens, pedantic)

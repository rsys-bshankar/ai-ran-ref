//go:build ignore

// go_tokens prints, for each Go file named on the command line, the file's token stream with all comments
// dropped, as one JSON object per line: {"file": "...", "canon": "tok\ntok...", "error": "..."}.
//
// It is the Go half of scripts/verify_docs_only.py: two versions of a file that produce the same canonical text
// differ in comments, blank lines and formatting only. It is run with `go run go_tokens.go FILE...` and uses the
// standard library alone (go/parser to prove the file is valid Go, go/scanner to read the tokens).
//
// Why tokens and not go/printer output: the printer keeps the blank lines of the source, so adding a comment line
// between two statements changes what it prints, and a docs-only change would be reported as a code change.
// Identical tokens (with the automatic semicolons the Go spec inserts at line ends) mean an identical syntax tree.
package main

import (
	"encoding/json"
	"fmt"
	"go/parser"
	"go/scanner"
	"go/token"
	"os"
	"strings"
)

// result is one line of output.
type result struct {
	File  string `json:"file"`
	Canon string `json:"canon"`
	Error string `json:"error,omitempty"`
}

// canon returns the comment-free token text of src, or an error if src is not valid Go.
func canon(path string, src []byte) (string, error) {
	fset := token.NewFileSet()
	// Mode 0: comments are not kept in the tree. Parsing proves the file is valid Go before its tokens are compared.
	if _, err := parser.ParseFile(fset, path, src, 0); err != nil {
		return "", err
	}
	var s scanner.Scanner
	file := token.NewFileSet().AddFile(path, -1, len(src))
	var scanErr error
	s.Init(file, src, func(pos token.Position, msg string) { scanErr = fmt.Errorf("%s: %s", pos, msg) }, 0) // mode 0: skip comments
	var b strings.Builder
	for {
		_, tok, lit := s.Scan()
		if tok == token.EOF {
			break
		}
		if tok == token.SEMICOLON {
			// An automatic semicolon has the literal "\n"; an explicit one ";". They mean the same thing.
			lit = ";"
		}
		b.WriteString(tok.String())
		b.WriteByte(' ')
		b.WriteString(lit)
		b.WriteByte('\n')
	}
	return b.String(), scanErr
}

// main prints one JSON line per file named on the command line.
func main() {
	enc := json.NewEncoder(os.Stdout)
	for _, path := range os.Args[1:] {
		r := result{File: path}
		src, err := os.ReadFile(path)
		if err == nil {
			r.Canon, err = canon(path, src)
		}
		if err != nil {
			r.Error = err.Error()
		}
		_ = enc.Encode(r)
	}
}

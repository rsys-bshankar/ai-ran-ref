//go:build ignore

// go_docs lists, for each Go file named on the command line, what scripts/check_code_docs.py needs to apply the
// documentation rule: whether the file has a description, and every function, method and type with its visibility,
// length and whether a doc comment is attached. One JSON object per line.
//
// It is run with `go run go_docs.go FILE...` and uses the standard library alone (go/parser, go/ast). The file name
// decides whether it is a test file (a name ending in _test.go); the decision about which items need a description is
// taken in Python so that every language shares one rule.
package main

import (
	"encoding/json"
	"go/ast"
	"go/parser"
	"go/token"
	"os"
	"strings"
	"unicode"
	"unicode/utf8"
)

// item is one declaration the rule may apply to.
type item struct {
	Name       string `json:"name"`
	Kind       string `json:"kind"` // "func", "method" or "type"
	Line       int    `json:"line"`
	Lines      int    `json:"lines"`
	Public     bool   `json:"public"`
	Trivial    bool   `json:"trivial"`
	Documented bool   `json:"documented"`
	Test       bool   `json:"test"`
}

// report is one line of output.
type report struct {
	File      string `json:"file"`
	Header    bool   `json:"header"`
	Generated bool   `json:"generated"`
	Empty     bool   `json:"empty"`
	Items     []item `json:"items"`
	Error     string `json:"error,omitempty"`
}

func exported(name string) bool {
	r, _ := utf8.DecodeRuneInString(name)
	return unicode.IsUpper(r)
}

// isDirective reports whether a comment group holds only compiler or tool directives (//go:build, // +build, //go:generate...),
// which describe nothing.
func isDirective(g *ast.CommentGroup) bool {
	for _, c := range g.List {
		t := strings.TrimSpace(strings.TrimPrefix(c.Text, "//"))
		if !(strings.HasPrefix(c.Text, "//go:") || strings.HasPrefix(t, "+build") || strings.HasPrefix(c.Text, "//line ") || strings.HasPrefix(c.Text, "//nolint")) {
			return false
		}
	}
	return true
}

// recvName returns the name of a method's receiver type, "" for a plain function.
func recvName(fd *ast.FuncDecl) string {
	if fd.Recv == nil || len(fd.Recv.List) == 0 {
		return ""
	}
	t := fd.Recv.List[0].Type
	for {
		switch x := t.(type) {
		case *ast.StarExpr:
			t = x.X
		case *ast.IndexExpr:
			t = x.X
		case *ast.IndexListExpr:
			t = x.X
		case *ast.Ident:
			return x.Name
		default:
			return ""
		}
	}
}

// isTestName reports whether name is a test, benchmark, fuzz target or example name (the prefix followed by an upper-case letter or nothing).
func isTestName(name string) bool {
	for _, p := range []string{"Test", "Benchmark", "Fuzz", "Example"} {
		if strings.HasPrefix(name, p) {
			rest := name[len(p):]
			if rest == "" {
				return true
			}
			r, _ := utf8.DecodeRuneInString(rest)
			return !unicode.IsLower(r)
		}
	}
	return false
}

func docText(g *ast.CommentGroup) bool {
	return g != nil && strings.TrimSpace(g.Text()) != ""
}

// analyse parses one file and returns what the documentation rule needs to know about it; a parse error is returned in the report, not raised.
func analyse(path string) report {
	r := report{File: path, Items: []item{}}
	fset := token.NewFileSet()
	f, err := parser.ParseFile(fset, path, nil, parser.ParseComments)
	if err != nil {
		r.Error = err.Error()
		return r
	}
	isTestFile := strings.HasSuffix(path, "_test.go")
	for _, g := range f.Comments {
		if strings.Contains(g.Text(), "Code generated") && strings.Contains(g.Text(), "DO NOT EDIT") {
			r.Generated = true
		}
	}
	r.Empty = len(f.Decls) == 0 && len(f.Comments) == 0

	// File description: a package comment, or any comment group (not a directive) that precedes the first declaration and is
	// not that declaration's own doc comment.
	var firstDoc *ast.CommentGroup
	firstPos := token.Pos(-1)
	if len(f.Decls) > 0 {
		firstPos = f.Decls[0].Pos()
		switch d := f.Decls[0].(type) {
		case *ast.FuncDecl:
			firstDoc = d.Doc
		case *ast.GenDecl:
			firstDoc = d.Doc
		}
	}
	if docText(f.Doc) && !isDirective(f.Doc) {
		r.Header = true
	}
	for _, g := range f.Comments {
		if isDirective(g) || g == firstDoc || !docText(g) {
			continue
		}
		if firstPos == token.Pos(-1) || g.End() < firstPos {
			r.Header = true
		}
	}

	line := func(p token.Pos) int { return fset.Position(p).Line }
	for _, d := range f.Decls {
		switch d := d.(type) {
		case *ast.FuncDecl:
			name := d.Name.Name
			kind := "func"
			public := exported(name)
			if rn := recvName(d); rn != "" {
				kind = "method"
				name = rn + "." + name
				public = public && exported(rn)
			}
			if name == "main" || name == "init" {
				public = false
			}
			n := line(d.End()) - line(d.Pos()) + 1
			trivial := n <= 5 && (d.Body == nil || len(d.Body.List) <= 1)
			r.Items = append(r.Items, item{
				Name: name, Kind: kind, Line: line(d.Pos()), Lines: n, Public: public, Trivial: trivial,
				Documented: docText(d.Doc), Test: isTestFile && kind == "func" && isTestName(d.Name.Name),
			})
		case *ast.GenDecl:
			if d.Tok != token.TYPE {
				continue
			}
			for _, sp := range d.Specs {
				ts := sp.(*ast.TypeSpec)
				doc := docText(ts.Doc) || (len(d.Specs) == 1 && docText(d.Doc))
				r.Items = append(r.Items, item{
					Name: ts.Name.Name, Kind: "type", Line: line(ts.Pos()), Lines: line(ts.End()) - line(ts.Pos()) + 1,
					Public: exported(ts.Name.Name), Documented: doc,
				})
			}
		}
	}
	return r
}

// main analyses every file named on the command line and prints one JSON line each.
func main() {
	enc := json.NewEncoder(os.Stdout)
	for _, path := range os.Args[1:] {
		_ = enc.Encode(analyse(path))
	}
}

// Package sample exercises the Go syntax the adapter relies on.
package sample

import (
	"errors"
	"fmt"
	"iter"
)

// Number is a type-set constraint.
type Number interface {
	~int | ~int64 | ~float64
}

// Stack is a generic container.
type Stack[T any] struct {
	items []T
	limit int
}

// Push adds an item, failing when the stack is full.
func (s *Stack[T]) Push(v T) error {
	if len(s.items) >= s.limit {
		return errors.New("full")
	}
	s.items = append(s.items, v)
	return nil
}

// All iterates over the items (range-over-func).
func (s *Stack[T]) All() iter.Seq2[int, T] {
	return func(yield func(int, T) bool) {
		for i, v := range s.items {
			if !yield(i, v) {
				return
			}
		}
	}
}

// Sum adds numbers of any numeric type.
func Sum[N Number](xs ...N) (total N) {
	for _, x := range xs {
		total += x
	}
	return
}

type Shape interface {
	Area() float64
	Name() string
}

func describe(v any, n int) (string, error) {
	switch x := v.(type) {
	case int, int64:
		return fmt.Sprint(x), nil
	case string:
		return x, nil
	default:
		return "", fmt.Errorf("unknown %T", v)
	}
}

func classify(n int) string {
	switch {
	case n < 0:
		return "negative"
	case n == 0:
		fallthrough
	case n < 10:
		return "small"
	}
	if n > 100 && n%2 == 0 || n == 7 {
		return "special"
	} else if n > 50 {
		return "large"
	} else {
		return "medium"
	}
}

func worker(ctx chan struct{}, jobs <-chan int, out chan<- int) {
	defer close(out)
outer:
	for {
		select {
		case j, ok := <-jobs:
			if !ok {
				break outer
			}
			out <- j * 2
		case <-ctx:
			return
		default:
			continue outer
		}
	}
	go func() {
		for range 10 {
		}
	}()
	goto done
done:
}

func loadAll(paths []string) ([]byte, error) {
	var buf []byte
	for i := 0; i < len(paths); i++ {
		data, err := read(paths[i])
		if err != nil {
			return nil, fmt.Errorf("load %s: %w", paths[i], err)
		}
		buf = append(buf, data...)
	}
	return buf, nil
}

var read = func(p string) ([]byte, error) { return []byte(p), nil }

func fact(n int) int {
	if n <= 1 {
		return 1
	}
	return n * fact(n-1)
}

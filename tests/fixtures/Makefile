CC ?= gcc
SRCS := $(wildcard src/*.c)
OBJS = $(SRCS:.c=.o)
MODE := $(if $(DEBUG),debug,release)

ifeq ($(DEBUG),1)
  CFLAGS += -g
else ifeq ($(OS),Windows_NT)
  CFLAGS += -O1
else
  ifdef PROFILE
    CFLAGS += -pg
  endif
  CFLAGS += -O2
endif

.PHONY: all clean test

all: app

app: $(OBJS)
	$(CC) -o $@ $^ $(LDFLAGS)

%.o: %.c
	$(CC) $(CFLAGS) -c $< -o $@

test: app
	@if [ -d tests ] && [ -x app ]; then \
	  for t in tests/*.sh; do \
	    if [ -s $$t ]; then \
	      sh $$t || exit 1; \
	    fi; \
	  done; \
	else echo "no tests"; fi

define run_tool
if [ "$(MODE)" = debug ]; then echo debug; else echo release; fi
endef

clean:
	-rm -rf $(OBJS) app

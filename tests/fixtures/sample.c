/* Sample C module for the parser spike and adapter tests. */
#include <stdint.h>
#include <stdlib.h>

#define MAX(a, b) ((a) > (b) ? (a) : (b))

struct point {
    int x;
    int y;
};

typedef enum { RED, GREEN } color_t;

static int clamp(int value, int lo, int hi) {
    if (value < lo) {
        return lo;
    } else if (value > hi) {
        return hi;
    } else {
        return value;
    }
}

int *find(int *items, size_t n, int wanted) {
    for (size_t i = 0; i < n; i++) {
        if (items[i] == wanted && wanted != 0) {
            return &items[i];
        }
    }
    return NULL;
}

const char *color_name(color_t c) {
    switch (c) {
    case RED:
        return "red";
    case GREEN:
        return "green";
    default:
        return "?";
    }
}

int cleanup(void) {
    int rc = 0;
    char *buf = malloc(16);
    if (!buf)
        goto fail;
    do {
        rc++;
    } while (rc < 3);
    while (rc > 0) {
        rc = rc > 1 ? rc - 2 : 0;
    }
    free(buf);
    return rc;
fail:
    return -1;
}

#ifdef DEBUG
void debug_dump(struct point p) {
    (void)p;
}
#else
void debug_dump(struct point p) {}
#endif

int (*pick(int which))(int, int, int) {
    return which ? clamp : NULL;
}

package com.example.shop;

import java.util.*;
import java.util.function.Function;

/** Orders and their pricing. */
@SuppressWarnings("unused")
public final class OrderService {
    private final Map<String, Order> orders = new HashMap<>();
    private int count; // orders seen

    static {
        System.out.println("loaded");
    }

    public OrderService(int count) {
        this.count = count;
    }

    public Optional<Order> find(String id) {
        return Optional.ofNullable(orders.get(id));
    }

    public int total(List<Order> items, boolean vip, String... codes) {
        int sum = 0;
        for (Order o : items) {
            if (o.amount() > 100 && vip || codes.length > 0) {
                sum += o.amount();
            } else if (o.amount() < 0) {
                continue;
            } else {
                sum -= 1;
            }
        }
        return sum;
    }

    String describe(Shape shape) {
        return switch (shape) {
            case Circle c when c.radius() > 10 -> "big circle";
            case Circle(double r) -> "circle " + r;
            case Square s -> "square";
            default -> "unknown";
        };
    }

    int legacy(int day) {
        switch (day) {
            case 1:
            case 2:
                return 1;
            default:
                return 0;
        }
    }

    void process(Object o) throws Exception {
        if (o instanceof String s && !s.isEmpty()) {
            outer:
            for (int i = 0; i < 10; i++) {
                while (i > 5) {
                    break outer;
                }
            }
        }
        try (var in = new java.io.StringReader("x")) {
            in.read();
        } catch (IllegalStateException | IllegalArgumentException e) {
            throw e;
        } finally {
            count++;
        }
        do {
            count--;
        } while (count > 0 ? true : false);
        Runnable r = () -> System.out.println(count);
        Function<Integer, Integer> twice = x -> {
            return x * 2;
        };
        Comparator<String> cmp = new Comparator<>() {
            @Override
            public int compare(String a, String b) {
                return a.compareTo(b);
            }
        };
        orders.values().stream().filter(v -> v.amount() > 1).map(Order::id).toList();
        var _ = """
            text block
            """;
    }

    record Order(String id, int amount) {
        Order {
            if (amount < 0) throw new IllegalArgumentException();
        }

        int doubled() {
            return amount * 2;
        }
    }

    sealed interface Shape permits Circle, Square {}

    record Circle(double radius) implements Shape {}

    record Square(double side) implements Shape {}

    enum Status {
        OPEN {
            @Override
            String label() {
                return "open";
            }
        },
        CLOSED;

        String label() {
            return name();
        }
    }

    interface Priced {
        int price();

        default int discounted(int pct) {
            return price() * (100 - pct) / 100;
        }
    }

    @interface Audited {
        String value() default "";
    }

    static <T extends Comparable<T>> T max(T a, T b) {
        return a.compareTo(b) >= 0 ? a : b;
    }
}

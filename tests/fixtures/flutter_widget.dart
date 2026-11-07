import 'package:flutter/material.dart';

// Dart 3 syntax the grammar must handle: sealed classes, records,
// patterns, switch expressions, class modifiers.

sealed class Shape {}

final class Circle extends Shape {
  Circle(this.radius);
  final double radius;
}

final class Square extends Shape {
  Square(this.side);
  final double side;
}

double area(Shape shape) => switch (shape) {
  Circle(radius: final r) => 3.14159 * r * r,
  Square(side: final s) => s * s,
};

(int, String) parseEntry(String raw) {
  final parts = raw.split(':');
  return (int.parse(parts[0]), parts[1]);
}

base class Counter {
  int value = 0;

  void bump({int by = 1, bool clamp = false, int max = 100}) {
    if (clamp) {
      for (var i = 0; i < by; i++) {
        if (value < max && !clamp || value == 0) {
          value++;
        }
      }
    } else {
      value += by;
    }
  }
}

class CounterPage extends StatefulWidget {
  const CounterPage({super.key, required this.title});

  final String title;

  @override
  State<CounterPage> createState() => _CounterPageState();
}

class _CounterPageState extends State<CounterPage> {
  int _count = 0;

  void _increment() {
    setState(() {
      _count++;
    });
  }

  @override
  Widget build(BuildContext context) {
    final (label, color) = _count.isEven
        ? ('even', Colors.blue)
        : ('odd', Colors.red);
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: [
            Text('$_count is $label', style: TextStyle(color: color)),
            if (_count > 3)
              Padding(padding: const EdgeInsets.all(8), child: Text('big')),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _increment,
        child: const Icon(Icons.add),
      ),
    );
  }
}

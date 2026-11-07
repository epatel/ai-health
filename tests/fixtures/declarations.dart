abstract class Repo<T> {
  Repo(this.name) {
    print(name);
  }
  Repo.named({required String id}) : name = id;
  factory Repo.make(String n) => throw n;
  final String name;
  int get size => 0;
  set size(int v) { print(v); }
  T? find(String id);
  bool operator ==(Object other) => false;
  Future<List<T>> load([int limit = 10, String? q]) async {
    int helper(int x) { return x * 2; }
    return [];
  }
  void each(void Function(T item) cb, Map<String, int> m) {}
}
mixin Logs on Object {
  void log(String m) {}
}
extension StrX on String {
  String twice() => this + this;
}
enum Color {
  red, green;
  bool get warm => this == red;
}
int get topGetter => 1;
void main() {}

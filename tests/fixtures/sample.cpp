// Sample C++ module for the parser spike and adapter tests.
#include <string>
#include <vector>

namespace shop {

class Cart {
public:
    Cart() : total_(0) {}
    ~Cart() {}

    void add(int price) {
        total_ += price;
        items_.push_back(price);
    }

    int total() const { return total_; }

    bool operator==(const Cart &other) const { return total_ == other.total_; }

    int discount(int percent) const;

private:
    int total_;
    std::vector<int> items_;
};

int Cart::discount(int percent) const {
    try {
        if (percent < 0 || percent > 100) {
            throw std::invalid_argument("percent");
        }
    } catch (const std::exception &e) {
        return 0;
    }
    int sum = 0;
    for (int item : items_) {
        sum += item;
    }
    auto apply = [percent](int v) { return v * (100 - percent) / 100; };
    return apply(sum);
}

template <typename T>
T largest(const std::vector<T> &values) {
    T best{};
    for (const auto &v : values) {
        best = v > best ? v : best;
    }
    return best;
}

}  // namespace shop

int main(int argc, char **argv) {
    shop::Cart cart;
    cart.add(argc);
    return cart.total();
}

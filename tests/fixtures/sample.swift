import SwiftUI

/// A user profile.
struct Profile: Identifiable, Codable {
    let id: UUID
    var name: String
    var age: Int?

    init(id: UUID = UUID(), name: String, age: Int? = nil) {
        self.id = id
        self.name = name
        self.age = age
    }

    var isAdult: Bool {
        guard let age else { return false }
        return age >= 18
    }

    var displayName: String {
        get { name.isEmpty ? "Anonymous" : name }
        set { name = newValue }
    }

    subscript(index: Int) -> Character {
        name[name.index(name.startIndex, offsetBy: index)]
    }

    static func == (lhs: Profile, rhs: Profile) -> Bool {
        lhs.id == rhs.id
    }
}

enum LoadError: Error {
    case notFound
    case network(code: Int)
}

protocol Store {
    func load(id: UUID) async throws -> Profile
}

actor ProfileCache {
    private var items: [UUID: Profile] = [:]

    func get(_ id: UUID) -> Profile? { items[id] }

    func put(_ profile: Profile) {
        items[profile.id] = profile
    }
}

final class ProfileLoader<S: Store>: ObservableObject {
    @Published var profile: Profile?
    private let store: S

    init(store: S) {
        self.store = store
    }

    deinit {
        print("bye")
    }

    func load(id: UUID, retries: Int = 3) async {
        outer: for attempt in 0..<retries {
            do {
                profile = try await store.load(id: id)
                break outer
            } catch LoadError.notFound {
                return
            } catch let LoadError.network(code) where code >= 500 && attempt < retries - 1 {
                continue
            } catch {
                print(error)
            }
        }
    }

    func classify(_ value: Int) -> String {
        switch value {
        case ..<0: return "negative"
        case 0: return "zero"
        case 1...9 where value % 2 == 0: return "small even"
        default: return "other"
        }
    }

    func sum(of numbers: [Int]) -> Int {
        func add(_ a: Int, _ b: Int) -> Int { a + b }
        var total = 0
        for n in numbers where n > 0 {
            if n > 100 || n == 42 {
                total = add(total, n * 2)
            } else if n > 10 {
                total = add(total, n)
            } else {
                total += 1
            }
        }
        repeat { total -= 1 } while total > 1000
        while total < 0 { total += 1 }
        return numbers.filter { $0 > 0 }.map { x in x * 2 }.reduce(total, +)
    }
}

extension Profile {
    func greeting(for other: Profile) -> String {
        if let age = other.age, age > 65 {
            return "Hello, \(other.name)"
        }
        let result = if age == nil { "Hi" } else { "Hey" }
        return result
    }
}

struct ProfileView: View {
    @State private var expanded = false
    let profile: Profile

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading) {
                Text(profile.name)
                    .font(.title)
                if expanded {
                    HStack {
                        Text("Age")
                        Spacer()
                        Text("\(profile.age ?? 0)")
                    }
                }
                Button("Toggle") { expanded.toggle() }
            }
            .padding()
        }
    }
}

func topLevel(_ x: Int, label y: String, _ rest: Int...) -> Int {
    let f: (Int) -> Int = { v in v + 1 }
    return x > 0 ? f(x) : -x
}

#Preview {
    ProfileView(profile: Profile(name: "Ada"))
}

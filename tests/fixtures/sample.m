// Sample Objective-C module for the parser spike and adapter tests.
#import <Foundation/Foundation.h>

@interface Greeter : NSObject
@property (nonatomic, copy) NSString *name;
- (NSString *)greet:(NSString *)who times:(NSInteger)n;
+ (instancetype)greeterWithName:(NSString *)name;
@end

@implementation Greeter {
    NSInteger _count;
}

+ (instancetype)greeterWithName:(NSString *)name {
    Greeter *g = [[Greeter alloc] init];
    g.name = name;
    return g;
}

- (NSString *)greet:(NSString *)who times:(NSInteger)n {
    NSMutableString *out = [NSMutableString string];
    for (NSInteger i = 0; i < n; i++) {
        if (who.length > 0 && _count < 10) {
            [out appendFormat:@"Hello %@ from %@\n", who, self.name];
        }
    }
    _count++;
    return out;
}

- (void)run {
    @try {
        [self greet:@"x" times:1];
    } @catch (NSException *e) {
        NSLog(@"%@", e);
    } @finally {
        _count = 0;
    }
    void (^block)(int) = ^(int v) {
        if (v) {
            NSLog(@"%d", v);
        }
    };
    block(1);
}

@end

static int helper(int a) {
    return a * 2;
}

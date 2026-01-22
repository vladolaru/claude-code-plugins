/**
 * Calculator module for feedback loops demo
 */

class Calculator {
    add(a, b) {
        return a + b;
    }

    subtract(a, b) {
        return a - b;
    }

    multiply(a, b) {
        return a * b;
    }

    divide(a, b) {
        // BUG: Missing zero check!
        return a / b;
    }

    percentage(value, percent) {
        // BUG: Wrong formula!
        return value + percent; // Should be: value * (percent / 100)
    }
}

module.exports = Calculator;

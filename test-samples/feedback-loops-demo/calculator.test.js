/**
 * Tests for Calculator
 *
 * Some tests will PASS, some will FAIL (due to bugs in calculator.js)
 * This demonstrates the value of feedback loops:
 * - Without test results: Agent might approve (code looks OK)
 * - With test results: Agent sees failures and blocks
 */

const Calculator = require('./calculator');

describe('Calculator', () => {
    let calc;

    beforeEach(() => {
        calc = new Calculator();
    });

    // PASSING TESTS
    describe('add', () => {
        it('should add two positive numbers', () => {
            expect(calc.add(2, 3)).toBe(5);
        });

        it('should add negative numbers', () => {
            expect(calc.add(-2, -3)).toBe(-5);
        });
    });

    describe('subtract', () => {
        it('should subtract two numbers', () => {
            expect(calc.subtract(5, 3)).toBe(2);
        });
    });

    describe('multiply', () => {
        it('should multiply two numbers', () => {
            expect(calc.multiply(4, 5)).toBe(20);
        });
    });

    // FAILING TESTS (due to bugs)
    describe('divide', () => {
        it('should divide two numbers', () => {
            expect(calc.divide(10, 2)).toBe(5);
        });

        it('should handle division by zero', () => {
            // FAILS: Bug in divide() - no zero check!
            expect(() => calc.divide(10, 0)).toThrow('Division by zero');
        });
    });

    describe('percentage', () => {
        it('should calculate percentage of a value', () => {
            // FAILS: Bug in percentage() - wrong formula!
            expect(calc.percentage(100, 10)).toBe(10); // Should be 10% of 100 = 10
        });

        it('should calculate 50% correctly', () => {
            // FAILS: Same bug
            expect(calc.percentage(200, 50)).toBe(100); // Should be 50% of 200 = 100
        });
    });
});

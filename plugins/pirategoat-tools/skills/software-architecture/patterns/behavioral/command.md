# Command Pattern

## Overview

The Command pattern transforms a function into a first-class citizen object, enabling it to leverage all object-oriented capabilities. By wrapping a function within a class and exposing it through a polymorphic interface, commands become more than just executable code - they can be queued, scheduled, logged, undone, and manipulated like any other object.

**Core Concept**: "Objectify" functions to decouple what is executed from who executes it and when it is executed.

## When to Use

Use the Command pattern when you encounter these scenarios:

- **Async Processing Required**: User-facing threads need fast response times, but operations take significant time to complete
- **Undo/Redo Functionality**: Applications like text processors, graphics editors, or any system requiring reversible operations
- **Action Queuing**: Operations need to be queued, scheduled, or executed at different times than when they're created
- **Logging and Auditing**: Need to maintain a history of all executed operations
- **Transactional Behavior**: Operations must be executed as transactions with rollback capabilities
- **Parameterization of Behavior**: Different objects need to be parameterized with different actions
- **Decoupling Invoker from Receiver**: The object requesting an operation should be decoupled from the object that knows how to perform it
- **Macro/Composite Actions**: Multiple operations need to be grouped and executed as a single command

## When NOT to Use

Avoid the Command pattern in these situations:

- **Simple Direct Calls Suffice**: When a simple method call is all you need, adding Command adds unnecessary complexity
- **No Need for History**: If you never need to undo, queue, log, or schedule operations
- **Performance-Critical Paths**: The additional object allocation and indirection may impact performance in tight loops
- **Stateless Single-Use Operations**: When operations have no parameters and are executed immediately
- **Tight Coupling is Acceptable**: When the invoker and receiver are intentionally tightly coupled and will change together

## Structure

### Key Components

**Command Interface**
- Declares the execution interface, typically a single `execute()` method
- May include additional methods like `undo()`, `redo()`, `isUndoable()`
- Represents "any action whatsoever" in abstract form

**ConcreteCommand**
- Implements the Command interface
- Encapsulates the receiver reference and operation parameters as state
- Binds the receiver object with the action to be performed
- Constructor accepts parameters that would normally be function arguments
- `execute()` method invokes corresponding operations on the receiver

**Receiver**
- The object that performs the actual work
- Contains the business logic for the operation
- Command delegates the actual execution to the receiver

**Invoker**
- Requests command execution without knowing the concrete command type
- May queue, schedule, log, or manage command lifecycle
- Completely decoupled from command implementation details

**Client**
- Creates concrete command objects
- Sets the command's receiver
- Passes commands to the invoker

**Command Processor** (Extended Pattern)
- Not in Gang of Four catalog (from POSA)
- Manages command scheduling, queuing, undo/redo stacks
- Provides framework for advanced command management

## How It Works

### Basic Execution Flow

1. **Client creates command**: Instantiates a ConcreteCommand with necessary parameters
2. **Client configures command**: Sets the receiver (if not done in constructor)
3. **Client passes to invoker**: Gives command to the invoker through the Command interface
4. **Invoker stores/queues command**: Holds reference without knowing concrete type
5. **Invoker executes**: Calls `command.execute()` at appropriate time
6. **Command delegates**: Forwards execution to receiver with stored parameters
7. **Receiver performs work**: Executes the actual business logic

### Undo/Redo Flow

**Execute Path**:
1. Command is executed via `execute()` method
2. Command is pushed onto `done` stack (maintains execution history)
3. Previous state is captured for potential undo

**Undo Path**:
1. Command is popped from `done` stack
2. Command's `undo()` method is invoked
3. Command is pushed onto `undone` stack (maintains undo history)

**Redo Path**:
1. Command is popped from `undone` stack
2. Command's `execute()` method is called again
3. Command is pushed back onto `done` stack

**New Command Path**:
1. New command is executed
2. `undone` stack is cleared (invalidates redo history)
3. New command is pushed onto `done` stack

## Real-World Examples

### Java's Runnable Interface

Java's `Runnable` is a real-world Command implementation with different nomenclature:
- Uses `run()` instead of `execute()`
- Contract: "may take any action whatsoever"
- Often associated with `Thread` class, but `Runnable` has no dependency on `Thread`
- Can be implemented as separate class, anonymous class, or lambda function
- Works with `Thread` and `ExecutorService` framework

### Thread and Executor Framework

**Thread**: Provides JVM framework for executing a `Runnable`
**Executor Interfaces**:
- `Executor`: Executes a Runnable command
- `ExecutorService`: Extends service management to Executor
- `ScheduledExecutorService`: Extends scheduling management to ExecutorService
**Executors Factory**: Provides factory methods to acquire various ExecutorServices

### Text Processor Actions

Text processors use Command for operations like:
- Make text bold (with undo to return to previous font)
- Cut/copy/paste operations
- Format changes
- Each action becomes a command that can be undone/redone

### GUI Action Processing

Controllers that process user actions:
- Each action type maps to a specific Command implementation
- Controller acquires command through factory method
- Controller executes command without knowing implementation details
- New actions can be added without modifying controller code

## Implementation Guide

### Basic Command Interface (PHP)

```php
<?php

/**
 * Command Interface
 *
 * Declares the execution interface for all commands.
 */
interface Command {
    public function execute(): void;
}
```

### Simple Concrete Command (PHP)

```php
<?php

/**
 * HelloWorld Command
 *
 * Simple command with no parameters - just prints a message.
 */
class HelloWorldCommand implements Command {
    public function execute(): void {
        echo "Hello World!\n";
    }
}

// Usage
$command = new HelloWorldCommand();
$command->execute();
```

### Parameterized Command (PHP)

```php
<?php

/**
 * Printer Command
 *
 * Demonstrates how to pass parameters to commands.
 * Function arguments become constructor arguments stored as state.
 */
class PrinterCommand implements Command {
    private string $text;

    public function __construct(string $text) {
        $this->text = $text;
    }

    public function execute(): void {
        echo $this->text . "\n";
    }
}

// Usage
$printer = new PrinterCommand("Hello There World!");
$printer->execute();
```

### Command with Receiver (PHP)

```php
<?php

/**
 * Document - The Receiver
 *
 * Contains the actual business logic for operations.
 */
class Document {
    private string $content = "";

    public function append(string $text): void {
        $this->content .= $text;
    }

    public function getContent(): string {
        return $this->content;
    }
}

/**
 * AppendTextCommand - Concrete Command
 *
 * Encapsulates a request to append text to a document.
 */
class AppendTextCommand implements Command {
    private Document $document;
    private string $text;

    public function __construct(Document $document, string $text) {
        $this->document = $document;
        $this->text = $text;
    }

    public function execute(): void {
        $this->document->append($this->text);
    }
}

// Usage
$doc = new Document();
$command = new AppendTextCommand($doc, "Hello World");
$command->execute();
echo $doc->getContent(); // Outputs: Hello World
```

### Factory-Based Command Acquisition (PHP)

```php
<?php

/**
 * Command Factory
 *
 * Decouples command creation from command usage.
 * Follows "Program to an interface, not an implementation" principle.
 */
class CommandFactory {
    public static function acquire(string $actionName, array $params = []): Command {
        switch ($actionName) {
            case 'print':
                return new PrinterCommand($params['text'] ?? 'Default text');
            case 'hello':
                return new HelloWorldCommand();
            default:
                throw new InvalidArgumentException("Unknown action: $actionName");
        }
    }
}

/**
 * Controller - The Invoker
 *
 * Processes actions without knowing concrete command types.
 */
class ActionController {
    public function processAction(string $action, array $params = []): void {
        $command = CommandFactory::acquire($action, $params);
        $command->execute();
    }
}

// Usage
$controller = new ActionController();
$controller->processAction('hello');
$controller->processAction('print', ['text' => 'Custom message']);
```

### Asynchronous Command Queue (PHP)

```php
<?php

/**
 * Command Queue - Simple Async Pattern
 *
 * Demonstrates decoupling "what" from "when".
 * In production, use actual queue systems like RabbitMQ, Redis, or Laravel Queues.
 */
class CommandQueue {
    private array $queue = [];

    public function add(Command $command): void {
        $this->queue[] = $command;
    }

    public function processAll(): void {
        while (!empty($this->queue)) {
            $command = array_shift($this->queue);
            $command->execute();
        }
    }
}

/**
 * Async Controller
 *
 * Adds commands to queue instead of executing immediately.
 */
class AsyncController {
    private CommandQueue $queue;

    public function __construct(CommandQueue $queue) {
        $this->queue = $queue;
    }

    public function processAction(string $action, array $params = []): void {
        $command = CommandFactory::acquire($action, $params);
        $this->queue->add($command);
        // Returns immediately - command executes later
    }
}

// Usage
$queue = new CommandQueue();
$controller = new AsyncController($queue);

// Queue multiple commands (fast, non-blocking)
$controller->processAction('print', ['text' => 'First']);
$controller->processAction('print', ['text' => 'Second']);
$controller->processAction('print', ['text' => 'Third']);

// Process queue when ready (potentially on different thread/process)
$queue->processAll();
```

### Undoable Command (PHP)

```php
<?php

/**
 * Undoable Command Interface
 *
 * Extends Command with undo capability.
 */
interface UndoableCommand extends Command {
    public function undo(): void;
    public function isUndoable(): bool;
}

/**
 * Text Style Command
 *
 * Makes text bold with ability to undo to previous style.
 */
class BoldTextCommand implements UndoableCommand {
    private TextEditor $editor;
    private int $startPos;
    private int $endPos;
    private string $previousStyle;

    public function __construct(TextEditor $editor, int $startPos, int $endPos) {
        $this->editor = $editor;
        $this->startPos = $startPos;
        $this->endPos = $endPos;
    }

    public function execute(): void {
        // Save current style before changing
        $this->previousStyle = $this->editor->getStyle($this->startPos, $this->endPos);
        $this->editor->applyStyle('bold', $this->startPos, $this->endPos);
    }

    public function undo(): void {
        $this->editor->applyStyle($this->previousStyle, $this->startPos, $this->endPos);
    }

    public function isUndoable(): bool {
        return true;
    }
}

/**
 * Simple Text Editor - The Receiver
 */
class TextEditor {
    private array $styles = [];

    public function applyStyle(string $style, int $start, int $end): void {
        for ($i = $start; $i <= $end; $i++) {
            $this->styles[$i] = $style;
        }
    }

    public function getStyle(int $start, int $end): string {
        // Simplified - assumes uniform style in range
        return $this->styles[$start] ?? 'normal';
    }
}
```

### Complete Undo/Redo Manager (PHP)

```php
<?php

/**
 * Command History Manager
 *
 * Manages command execution history with undo/redo capability.
 */
class CommandHistory {
    private array $doneStack = [];
    private array $undoneStack = [];

    public function executeCommand(UndoableCommand $command): void {
        $command->execute();

        if ($command->isUndoable()) {
            $this->doneStack[] = $command;
            // Clear redo stack - new command invalidates undo history
            $this->undoneStack = [];
        }
    }

    public function undo(): bool {
        if (empty($this->doneStack)) {
            return false;
        }

        $command = array_pop($this->doneStack);
        $command->undo();
        $this->undoneStack[] = $command;

        return true;
    }

    public function redo(): bool {
        if (empty($this->undoneStack)) {
            return false;
        }

        $command = array_pop($this->undoneStack);
        $command->execute();
        $this->doneStack[] = $command;

        return true;
    }

    public function canUndo(): bool {
        return !empty($this->doneStack);
    }

    public function canRedo(): bool {
        return !empty($this->undoneStack);
    }
}

// Usage
$history = new CommandHistory();
$editor = new TextEditor();

// Execute commands
$cmd1 = new BoldTextCommand($editor, 0, 5);
$history->executeCommand($cmd1);

$cmd2 = new BoldTextCommand($editor, 6, 10);
$history->executeCommand($cmd2);

// Undo last command
if ($history->canUndo()) {
    $history->undo();
}

// Redo
if ($history->canRedo()) {
    $history->redo();
}
```

### Complete Working Example: Document Editor (PHP)

```php
<?php

/**
 * Complete Document Editor Example
 * Demonstrates Command pattern with undo/redo in a text editor context.
 */

// ============================================================================
// Receiver Layer
// ============================================================================

class Document {
    private string $content;

    public function __construct(string $initialContent = "") {
        $this->content = $initialContent;
    }

    public function insertText(int $position, string $text): void {
        $this->content = substr_replace($this->content, $text, $position, 0);
    }

    public function deleteText(int $position, int $length): void {
        $this->content = substr_replace($this->content, '', $position, $length);
    }

    public function getContent(): string {
        return $this->content;
    }

    public function getLength(): int {
        return strlen($this->content);
    }
}

// ============================================================================
// Command Layer
// ============================================================================

interface DocumentCommand extends UndoableCommand {
    // Marker interface for document commands
}

class InsertTextCommand implements DocumentCommand {
    private Document $document;
    private int $position;
    private string $text;

    public function __construct(Document $document, int $position, string $text) {
        $this->document = $document;
        $this->position = $position;
        $this->text = $text;
    }

    public function execute(): void {
        $this->document->insertText($this->position, $this->text);
    }

    public function undo(): void {
        $this->document->deleteText($this->position, strlen($this->text));
    }

    public function isUndoable(): bool {
        return true;
    }
}

class DeleteTextCommand implements DocumentCommand {
    private Document $document;
    private int $position;
    private int $length;
    private string $deletedText;

    public function __construct(Document $document, int $position, int $length) {
        $this->document = $document;
        $this->position = $position;
        $this->length = $length;
        // Capture text before deletion for undo
        $content = $document->getContent();
        $this->deletedText = substr($content, $position, $length);
    }

    public function execute(): void {
        $this->document->deleteText($this->position, $this->length);
    }

    public function undo(): void {
        $this->document->insertText($this->position, $this->deletedText);
    }

    public function isUndoable(): bool {
        return true;
    }
}

class MacroCommand implements DocumentCommand {
    private array $commands = [];
    private string $name;

    public function __construct(string $name) {
        $this->name = $name;
    }

    public function addCommand(DocumentCommand $command): void {
        $this->commands[] = $command;
    }

    public function execute(): void {
        foreach ($this->commands as $command) {
            $command->execute();
        }
    }

    public function undo(): void {
        // Undo in reverse order
        foreach (array_reverse($this->commands) as $command) {
            $command->undo();
        }
    }

    public function isUndoable(): bool {
        return true;
    }
}

// ============================================================================
// Invoker Layer
// ============================================================================

class DocumentEditor {
    private Document $document;
    private CommandHistory $history;

    public function __construct(Document $document) {
        $this->document = $document;
        $this->history = new CommandHistory();
    }

    public function insertText(int $position, string $text): void {
        $command = new InsertTextCommand($this->document, $position, $text);
        $this->history->executeCommand($command);
    }

    public function deleteText(int $position, int $length): void {
        $command = new DeleteTextCommand($this->document, $position, $length);
        $this->history->executeCommand($command);
    }

    public function undo(): bool {
        return $this->history->undo();
    }

    public function redo(): bool {
        return $this->history->redo();
    }

    public function getContent(): string {
        return $this->document->getContent();
    }
}

// ============================================================================
// Client/Usage
// ============================================================================

// Create document and editor
$doc = new Document();
$editor = new DocumentEditor($doc);

// Perform operations
$editor->insertText(0, "Hello");
echo "After insert: " . $editor->getContent() . "\n"; // Hello

$editor->insertText(5, " World");
echo "After insert: " . $editor->getContent() . "\n"; // Hello World

$editor->deleteText(5, 6);
echo "After delete: " . $editor->getContent() . "\n"; // Hello

// Undo
$editor->undo();
echo "After undo: " . $editor->getContent() . "\n"; // Hello World

// Redo
$editor->redo();
echo "After redo: " . $editor->getContent() . "\n"; // Hello

// Undo multiple times
$editor->undo(); // Undo delete
$editor->undo(); // Undo second insert
echo "After 2 undos: " . $editor->getContent() . "\n"; // Hello
```

### Common Variations

**Macro Commands (Composite Command)**:
```php
<?php

// Multiple commands grouped as one
$macro = new MacroCommand("Format Paragraph");
$macro->addCommand(new BoldTextCommand($editor, 0, 10));
$macro->addCommand(new ItalicTextCommand($editor, 0, 10));
$macro->addCommand(new AlignTextCommand($editor, 'center'));
$history->executeCommand($macro);
// Single undo will reverse all three operations
```

**Queued Commands**:
```php
<?php

// Commands added to queue for batch processing
$queue->add(new SendEmailCommand($recipient, $subject, $body));
$queue->add(new LogCommand("Email queued"));
$queue->add(new UpdateDatabaseCommand($userId, $status));
// Process later, potentially on different server
```

**Logged Commands**:
```php
<?php

class LoggingCommandDecorator implements Command {
    private Command $command;
    private Logger $logger;

    public function __construct(Command $command, Logger $logger) {
        $this->command = $command;
        $this->logger = $logger;
    }

    public function execute(): void {
        $this->logger->log("Executing: " . get_class($this->command));
        $this->command->execute();
        $this->logger->log("Completed: " . get_class($this->command));
    }
}
```

**JavaScript/Node.js Adaptation**:

All PHP examples can be adapted to JavaScript/TypeScript:
```javascript
// Command Interface (TypeScript)
interface Command {
    execute(): void;
}

// Concrete Command
class PrinterCommand implements Command {
    constructor(private text: string) {}

    execute(): void {
        console.log(this.text);
    }
}

// Usage
const cmd = new PrinterCommand("Hello World");
cmd.execute();
```

## Benefits

### Decoupling

- **Invoker-Receiver Separation**: Object requesting operation doesn't need to know about object performing it
- **What vs. Whom**: Execution logic separated from decision-making logic
- **What vs. When**: Command creation time separated from execution time

### Extensibility

- **Open/Closed Principle**: Add new commands without modifying invoker or receiver
- **Modular Design**: Each command is self-contained and independently testable
- **Easy Feature Addition**: New actions added as new command classes

### Flexibility

- **First-Class Citizens**: Commands can be passed, stored, queued, logged like any object
- **Runtime Configuration**: Commands can be selected and composed at runtime
- **Parameterization**: Objects can be parameterized with different actions

### Advanced Capabilities

- **Undo/Redo**: Commands maintain state enabling operation reversal
- **Macro Commands**: Composite pattern allows grouping multiple commands
- **Queueing/Scheduling**: Commands can be queued for later execution
- **Logging/Auditing**: Command history provides audit trail
- **Transactional Behavior**: Commands can implement rollback semantics

### Testability

- **Isolated Testing**: Each command can be unit tested independently
- **Mock Receivers**: Easy to mock receivers for command testing
- **Testable History**: Undo/redo logic can be thoroughly tested

## Trade-offs

### Complexity Costs

- **Additional Classes**: Each operation requires a new command class
- **Indirection Overhead**: Extra layer between invoker and receiver
- **Learning Curve**: Team must understand command pattern concepts

### Performance Impact

- **Object Allocation**: Creates objects for every command invocation
- **Memory Overhead**: History stacks consume memory (especially with undo/redo)
- **Execution Time**: Slight overhead from polymorphic dispatch

### Development Overhead

- **More Code**: Simple operations become multi-class implementations
- **Maintenance**: More classes to maintain and update
- **Over-Engineering Risk**: Pattern may be overkill for simple scenarios

### State Management Challenges

- **Undo Complexity**: Not all operations can be undone (cannot "unring a bell")
- **State Capture**: Commands must carefully capture state for undo
- **Memory Growth**: Long-running applications accumulate command history

### When Simplicity Matters

- **Direct Calls Preferred**: Sometimes a simple method call is clearer
- **YAGNI Principle**: Don't add complexity for features you might never need
- **Performance Critical**: Hot paths may not tolerate indirection

## Common Mistakes

### Mistake 1: Violating "Program to Interface" Principle

**Wrong**:
```php
// Client creates concrete command directly
$command = new PrinterCommand("Hello");
$command->execute();
```

**Right**:
```php
// Client uses factory to get command through interface
$command = CommandFactory::acquire('print', ['text' => 'Hello']);
$command->execute();
```

**Why**: Direct instantiation couples client to concrete command implementation.

### Mistake 2: Forgetting to Clear Undo Stack on New Command

**Wrong**:
```php
public function executeCommand(UndoableCommand $command): void {
    $command->execute();
    $this->doneStack[] = $command;
    // BUG: undoneStack should be cleared
}
```

**Right**:
```php
public function executeCommand(UndoableCommand $command): void {
    $command->execute();
    $this->doneStack[] = $command;
    $this->undoneStack = []; // Clear redo history
}
```

**Why**: New command invalidates previous undo history - redo no longer makes sense.

### Mistake 3: Not Capturing State Before Execution

**Wrong**:
```php
public function execute(): void {
    // Delete without saving what was deleted
    $this->document->deleteText($this->position, $this->length);
}

public function undo(): void {
    // No way to restore deleted content
    $this->document->insertText($this->position, "???");
}
```

**Right**:
```php
public function __construct(Document $doc, int $pos, int $len) {
    $this->document = $doc;
    $this->position = $pos;
    $this->length = $len;
    // Capture state BEFORE making changes
    $this->deletedText = substr($doc->getContent(), $pos, $len);
}
```

**Why**: Undo requires knowledge of previous state.

### Mistake 4: Blocking User Thread with Long-Running Commands

**Wrong**:
```php
public function processAction(string $action): void {
    $command = CommandFactory::acquire($action);
    $command->execute(); // Blocks if command takes long time
}
```

**Right**:
```php
public function processAction(string $action): void {
    $command = CommandFactory::acquire($action);
    $this->queue->add($command); // Non-blocking
    // Another thread/process executes queued commands
}
```

**Why**: User interface must remain responsive.

### Mistake 5: Confusing Command with Strategy Pattern

**Wrong Understanding**: "Command and Strategy are the same - both use polymorphism"

**Right Understanding**:
- **Command**: Encapsulates a request/action with parameters (verb-oriented)
- **Strategy**: Encapsulates an algorithm/approach (noun-oriented)
- Command focuses on "what to do" (action)
- Strategy focuses on "how to do it" (algorithm)

### Mistake 6: Making Commands Stateless

**Wrong**:
```php
class SaveCommand implements Command {
    public function execute(): void {
        // No receiver, no parameters - how does this save anything?
        global $document; // Anti-pattern
        $document->save();
    }
}
```

**Right**:
```php
class SaveCommand implements Command {
    private Document $document;
    private string $filename;

    public function __construct(Document $document, string $filename) {
        $this->document = $document;
        $this->filename = $filename;
    }

    public function execute(): void {
        $this->document->save($this->filename);
    }
}
```

**Why**: Commands encapsulate receivers and parameters as state.

### Mistake 7: Over-Using Anonymous Functions

**Wrong** (for complex logic):
```php
$executor->submit(function() {
    // 50 lines of complex logic
    // Difficult to test
    // Cannot be reused
});
```

**Right**:
```php
class ComplexOperation implements Command {
    // Separate class can be unit tested
    // Can be reused
    // Clear naming conveys intent
}
$executor->submit(new ComplexOperation());
```

**Why**: Complex commands should be separate classes for testability and reusability.

## Pattern Relationships

### Extends

- **Command Processor** (POSA pattern): Builds on Command to provide scheduling, queuing, undo/redo management
- **Macro Command**: Uses Composite pattern to create commands that execute other commands

### Works With

- **Factory Method**: Creates commands without exposing concrete classes
  - Example: `CommandFactory::acquire()`
- **Composite**: Enables macro commands (multiple commands as one)
  - Example: `MacroCommand` containing multiple sub-commands
- **Memento**: Stores command state for undo operations
  - Example: Capturing document state before modification
- **Prototype**: Clones commands for retry or variations
  - Example: Duplicate last command with different parameters
- **Chain of Responsibility**: Commands can be chained for processing
  - Example: Command pipeline with multiple processing stages
- **Observer**: Notify observers when commands execute
  - Example: UI updates when command completes
- **Decorator**: Wrap commands with additional behavior
  - Example: Logging, timing, security checks around command execution

### Alternative To

- **Direct Method Calls**: When no queueing/undo/logging needed
  - Command adds indirection for added capabilities
  - Use direct calls when simplicity matters
- **Strategy Pattern**: Different intent but similar structure
  - Strategy: Algorithm selection (how to do something)
  - Command: Action encapsulation (what to do)
- **Callback Functions**: In languages with first-class functions
  - Commands provide more structure and state management
  - Callbacks simpler for basic scenarios

### Related Patterns

- **Interpreter**: Commands can represent expressions in a language
- **Visitor**: Commands can implement visitor operations
- **Template Method**: Commands can use template method for execution flow

## Decision Criteria

### Command vs. Direct Method Calls

| Use Command When... | Use Direct Calls When... |
|---------------------|--------------------------|
| Need to queue/schedule operations | Operations execute immediately |
| Need undo/redo capability | No need to reverse operations |
| Need to log/audit operations | No audit requirements |
| Want to decouple invoker from receiver | Tight coupling is acceptable |
| Operations are first-class entities | Operations are simple utilities |
| Need to parameterize objects with operations | Operations are fixed at compile time |

### Command vs. Strategy

| Command | Strategy |
|---------|----------|
| Encapsulates a request/action | Encapsulates an algorithm |
| Verb-oriented (Save, Print, Delete) | Noun-oriented (SortStrategy, CompressionStrategy) |
| Often includes receiver reference | Usually operates on data passed to it |
| Supports undo/redo | Typically stateless |
| Queued, scheduled, logged | Selected and executed immediately |
| Focus: What action to perform | Focus: How to perform action |

### Command vs. Callback/Lambda

| Use Command When... | Use Callback/Lambda When... |
|---------------------|------------------------------|
| Need undo/redo | Single execution only |
| Complex logic requiring tests | Simple, obvious logic |
| Need to queue for later execution | Execute immediately |
| Need to maintain state | Stateless or uses closure |
| Want explicit class with clear name | Quick inline functionality |

### Undo/Redo: Command vs. Memento

| Command Approach | Memento Approach |
|------------------|------------------|
| Commands know how to undo themselves | Mementos store complete state snapshots |
| More efficient memory-wise | Can be memory intensive |
| Commands must implement undo logic | Simpler - just restore state |
| Can't undo if state not captured | Can always undo to saved state |
| Better for fine-grained operations | Better for coarse-grained checkpoints |

### Async Processing: Command vs. Message Queue

| Command Pattern | Message Queue System |
|-----------------|----------------------|
| In-process or simple queue | Distributed, fault-tolerant |
| Synchronous or basic async | Production-grade async |
| Lower complexity | Higher complexity |
| Single application | Multi-service architecture |
| Direct object references | Serialized messages |
| Development/testing | Production systems |

## Quotes

> "Everything is an object in Java."
>
> Popular saying about Java's object-oriented nature

> "A function wrapped within an object can do anything an object can do."
>
> Core insight about Command pattern's power

> "The general contract of the method run is that it may take any action whatsoever."
>
> Java Runnable documentation - unrestricted freedom

> "By 'objectifying' the function, it becomes a first-class citizen."
>
> Describing Command's transformation of functions

> "Command Design Pattern allows us to wrap a function within a class, instantiate it as an object and then execute it through an Interface via polymorphism."
>
> Technical definition of Command pattern

> "It's about intent, context and mindset. Once a function is an object, we can do much more with it."
>
> Philosophy behind Command pattern

> "This decouples what is being executed from the code that is executing it."
>
> Command's decoupling benefit

> "Command has a trick up its sleeve that functions do not have. Since Command is an object, it can have state."
>
> Key advantage enabling undo/redo

> "NOTE: Some functions cannot be undone. You can't unring a bell."
>
> Important limitation of undo capability

## Further Reading

### Free Online Resources

- **Wikipedia Command Design Pattern**: https://en.wikipedia.org/wiki/Command_pattern
  - Overview, structure, and examples
- **Source Making Command Design Pattern**: https://sourcemaking.com/design_patterns/command
  - Detailed explanations with code examples
- **Refactoring Guru Command Design pattern**: https://refactoring.guru/design-patterns/command
  - Visual diagrams and implementations in multiple languages
- **DoFactory Command Design Pattern**: https://www.dofactory.com/net/command-design-pattern
  - .NET-focused examples
- **Project Management Institute Command Design Pattern**: https://www.pmi.org/disciplined-agile/the-design-patterns-repository/the-command-pattern
  - Agile context for Command pattern
- **The Evolution of Command Pattern** by Guowei Lv:
  - Part I: https://www.lvguowei.me/post/the-evolution-of-command-pattern/
    - How Command pattern has evolved over time
  - Part II: https://www.lvguowei.me/post/the-evolution-of-command-pattern-2/
    - Implementation of undo/redo mechanisms
- **The Command Processor Design Pattern**: https://www.dre.vanderbilt.edu/~schmidt/cs282/PDFs/CommandProcessor.pdf
  - POSA pattern that extends Command

### Books and Paid Resources

- **Gang of Four: Design Patterns - Elements of Reusable Object-Oriented Software**
  - O'Reilly: https://learning.oreilly.com/library/view/design-patterns-elements/0201633612/ch05.html#page_233
  - Original Command pattern documentation
- **Agile Principles, Patterns, and Practices in C#** by Robert C. Martin, Chapter 21
  - O'Reilly: https://learning.oreilly.com/library/view/agile-principles-patterns/0131857258/
  - Amazon: https://www.amazon.com/Agile-Principles-Patterns-Practices-C/dp/0131857258
  - Changed author's perspective on Command pattern
- **Clean Code: Design Patterns**, Episode 25 by Robert C. Martin
  - Clean Coders: https://cleancoders.com/episode/clean-code-episode-25
  - O'Reilly: https://learning.oreilly.com/videos/clean-code-fundamentals/9780134661742/9780134661742-code_03_25_00/
  - Video explanation of Command pattern
- **Head First Design Patterns**
  - O'Reilly: https://learning.oreilly.com/library/view/head-first-design/9781492077992/ch06.html#home_automation_or_bust
  - Amazon: https://www.amazon.com/Head-First-Design-Patterns-Object-Oriented-ebook/dp/B08P3X99QP
  - Beginner-friendly Command pattern explanation
- **Pattern-Oriented Software Architecture (POSA) Series**
  - Wikipedia: https://en.wikipedia.org/wiki/Pattern-Oriented_Software_Architecture
  - Source of Command Processor pattern

### Additional Search

- Google: "Command Design Pattern" - https://www.google.com/search?q=command+design+pattern
  - Find more tutorials, examples, and discussions

### Java-Specific Resources

- **Java Runnable Interface**: https://docs.oracle.com/javase/8/docs/api/java/lang/Runnable.html
  - Real-world Command pattern in Java standard library
- **Java Thread Class**: https://docs.oracle.com/javase/8/docs/api/java/lang/Thread.html
  - Framework for executing Runnable commands
- **Java Executor Interface**: https://docs.oracle.com/javase/8/docs/api/java/util/concurrent/Executor.html
  - Executes Runnable commands
- **Java ExecutorService Interface**: https://docs.oracle.com/javase/8/docs/api/java/util/concurrent/ExecutorService.html
  - Extended service management for command execution
- **Java ScheduledExecutorService Interface**: https://docs.oracle.com/javase/8/docs/api/java/util/concurrent/ScheduledExecutorService.html
  - Scheduling management for commands
- **Java Executors Factory Class**: https://docs.oracle.com/javase/8/docs/api/java/util/concurrent/Executors.html
  - Factory methods for ExecutorServices
- **Baeldung: A Guide to the Java ExecutorService**: https://www.baeldung.com/java-executor-service-tutorial
  - Practical tutorial for Java's command execution framework

---

## Notes on Language Adaptability

All code examples are provided in PHP but follow language-agnostic principles. The pattern translates naturally to:

- **JavaScript/TypeScript**: Use classes or prototype-based objects
- **Python**: Use classes with duck typing
- **Java**: Native support with strong typing
- **C#**: Similar to Java with added features
- **Ruby**: Use classes or modules
- **Go**: Use interfaces and structs
- **Rust**: Use traits and structs

The core concepts remain the same across languages:
1. Define command interface with execute method
2. Implement concrete commands with encapsulated receivers
3. Invoker works through command interface
4. Commands are first-class objects

Adjust syntax and idioms to match your language while preserving the pattern's intent.

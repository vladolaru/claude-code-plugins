# Command Pattern

## Overview

The Command pattern transforms a function into a first-class object, enabling it to be queued, scheduled, logged, undone, and manipulated like any other object. It decouples **what** is executed from **who** executes it and **when** it executes.

**Components:** Command interface (`execute()`), ConcreteCommand (encapsulates receiver + params as state), Receiver (performs actual work), Invoker (triggers execution without knowing concrete type), Client (creates and configures commands).

## When to Use

- **Undo/redo** -- text editors, graphics apps, any system requiring reversible operations
- **Async processing** -- operations queued for later execution on different thread/process
- **Action queuing/scheduling** -- batch processing, deferred execution
- **Logging and auditing** -- maintain history of all executed operations
- **Transactional behavior** -- operations with rollback capabilities
- **Macro/composite actions** -- group multiple operations as a single undoable command
- **Decoupling invoker from receiver** -- invoker works through interface, unaware of concrete commands

## When NOT to Use

- **Simple direct calls suffice** -- no need for history, queuing, or undo
- **Stateless single-use operations** -- executed immediately with no parameters
- **Performance-critical tight loops** -- object allocation overhead matters
- **No audit/history requirements** -- adding Command just adds unnecessary indirection

## WordPress/PHP Example: WP-CLI Style Command with Undo

```php
interface Command {
    public function execute(): void;
}

interface UndoableCommand extends Command {
    public function undo(): void;
}

// Receiver
class Document {
    private string $content = "";
    public function insertText(int $position, string $text): void {
        $this->content = substr_replace($this->content, $text, $position, 0);
    }
    public function deleteText(int $position, int $length): void {
        $this->content = substr_replace($this->content, '', $position, $length);
    }
    public function getContent(): string { return $this->content; }
}

// Concrete commands with state for undo
class InsertTextCommand implements UndoableCommand {
    public function __construct(
        private Document $document,
        private int $position,
        private string $text
    ) {}

    public function execute(): void {
        $this->document->insertText($this->position, $this->text);
    }

    public function undo(): void {
        $this->document->deleteText($this->position, strlen($this->text));
    }
}

class DeleteTextCommand implements UndoableCommand {
    private string $deletedText;

    public function __construct(
        private Document $document,
        private int $position,
        private int $length
    ) {
        $this->deletedText = substr($document->getContent(), $position, $length);
    }

    public function execute(): void {
        $this->document->deleteText($this->position, $this->length);
    }

    public function undo(): void {
        $this->document->insertText($this->position, $this->deletedText);
    }
}

// Command history manager (invoker)
class CommandHistory {
    private array $doneStack = [];
    private array $undoneStack = [];

    public function executeCommand(UndoableCommand $command): void {
        $command->execute();
        $this->doneStack[] = $command;
        $this->undoneStack = []; // New command invalidates redo history
    }

    public function undo(): bool {
        if (empty($this->doneStack)) return false;
        $command = array_pop($this->doneStack);
        $command->undo();
        $this->undoneStack[] = $command;
        return true;
    }

    public function redo(): bool {
        if (empty($this->undoneStack)) return false;
        $command = array_pop($this->undoneStack);
        $command->execute();
        $this->doneStack[] = $command;
        return true;
    }
}
```

## JS/TS Interface

```typescript
interface Command {
    execute(): void;
    undo?(): void;
}

class PrinterCommand implements Command {
    constructor(private text: string) {}
    execute(): void { console.log(this.text); }
}

// Macro command (Composite pattern)
class MacroCommand implements Command {
    private commands: Command[] = [];
    add(cmd: Command): void { this.commands.push(cmd); }
    execute(): void { this.commands.forEach(c => c.execute()); }
    undo(): void { [...this.commands].reverse().forEach(c => c.undo?.()); }
}
```

## Common Mistakes

- **WRONG:** Not clearing undo stack on new command execution (stale redo history)
  **RIGHT:** Clear `undoneStack` whenever a new command is executed

- **WRONG:** Not capturing state before execution (cannot undo delete without saved text)
  **RIGHT:** Capture previous state in constructor or before `execute()` modifies it

- **WRONG:** Stateless commands using globals (`global $document`)
  **RIGHT:** Commands encapsulate receiver and parameters as constructor-injected state

- **WRONG:** Blocking user thread with long-running commands
  **RIGHT:** Queue commands for async execution; return immediately to caller

- **WRONG:** Confusing Command with Strategy ("both use polymorphism")
  **RIGHT:** Command = objectified action (verb-oriented, stateful, undo/queue). Strategy = interchangeable algorithm (noun-oriented, often stateless).

## Relationships

- **Composite** -- MacroCommand groups sub-commands as one undoable unit
- **Factory Method** -- creates commands without exposing concrete classes (`CommandFactory::acquire()`)
- **Memento** -- alternative undo approach: store complete state snapshots vs. command-based reversal
- **Decorator** -- wrap commands with logging, timing, security checks
- **Strategy** -- similar structure, different intent: Command encapsulates *action*; Strategy encapsulates *algorithm*

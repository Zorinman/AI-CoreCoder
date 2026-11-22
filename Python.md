# 安装 Python 项目的依赖管理

多数 Python 项目都会使用 `pip install` 安装项目本身及其依赖。对于本地项目，`pip` 会读取项目根目录中的包配置文件；该文件定义了项目名称、版本和依赖等信息。

- **旧方式**：`setup.py`
- **现代方式**：`pyproject.toml`

## 三种安装方式对比

`pip install -e .`、`pip install .` 与 `pip install corecoder` 的核心差异，在于安装来源、项目代码的位置，以及修改本地代码后是否会生效。

| 对比维度       | `pip install -e .`                              | `pip install .`                                                    | `pip install corecoder`                             |
| :------------- | :---------------------------------------------- | :----------------------------------------------------------------- | :-------------------------------------------------- |
| 安装来源       | 当前本地目录中的 `pyproject.toml` 或 `setup.py` | 当前本地目录中的 `pyproject.toml` 或 `setup.py`                    | PyPI（Python Package Index）上发布的 `corecoder` 包 |
| 安装模式       | 可编辑模式（`-e`），为本地源码创建引用          | 本地普通安装，将构建后的项目代码安装到当前 Python 环境             | 普通模式，将已发布包的代码安装到当前 Python 环境    |
| 修改项目代码后 | 通常立即生效；下次导入时会读取本地源码          | 不会生效；需重新执行 `pip install .`，必要时加 `--force-reinstall` | 不会生效；需重新安装包含修改的新版本                |
| 典型使用场景   | 开发和调试 `corecoder`，一边修改一边验证        | 在本地验证普通安装后的实际行为                                     | 只使用 `corecoder`，不修改其源码                    |

## `site-packages` 是什么

`site-packages` 是当前 Python 环境存放已安装第三方包及其元数据的目录。无论使用哪种安装方式，项目依赖通常都会安装到这里；但项目自身的源码位置会因安装方式而不同。

| 环境类型                      | 典型路径                                    |
| :---------------------------- | :------------------------------------------ |
| 系统 Python（Windows）        | `C:\Python312\Lib\site-packages`            |
| 系统 Python（macOS/Linux）    | `/usr/local/lib/python3.x/site-packages`    |
| 虚拟环境（venv，Windows）     | `项目目录\venv\Lib\site-packages`           |
| 虚拟环境（venv，macOS/Linux） | `项目目录/venv/lib/python3.x/site-packages` |

## `site-packages` 的详细操作对比

| 命令                                            | 对 `site-packages` 做了什么                                                                                                                                                                                                                                                                       | 项目源码在哪里                                                                                   | 项目代码修改后的生效情况                                                                                                                                                 |
| :---------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :----------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **`pip install -e .`**<br>（安装本地项目）      | **不复制源代码**：不会将项目源码复制到 `site-packages`。<br><br>**创建链接**：在 `site-packages` 中生成较小的 `.pth` 链接文件，指向硬盘上的本地项目源码目录。<br><br>**安装依赖包**：读取本地 `pyproject.toml` 中的依赖声明，将所需第三方依赖包（如 `requests`、`numpy`）安装到 `site-packages`。 | 始终位于本地项目文件夹中，例如 `D:\my_projects\corecoder`。                                      | **立即生效**：Python 通过 `.pth` 文件找到了本地源码路径，所以每次导入时都会直接读取本地文件，修改会实时反映。已加载的模块需重新导入或重启 Python 解释器。                |
| **`pip install .`**<br>（本地普通安装）         | **构建并安装项目代码**：根据本地 `pyproject.toml` 或 `setup.py` 构建项目，再将构建后的代码安装到 `site-packages`。<br><br>**安装依赖包**：读取本地项目的依赖声明，将所需第三方依赖包安装到 `site-packages`。                                                                                      | 被复制到 Python 环境的 `site-packages` 目录中，例如 `C:\Python312\Lib\site-packages\corecoder`。 | **不生效，需要重新安装**：Python 从 `site-packages` 中的副本读取代码。修改本地项目文件夹后，需重新执行 `pip install .`；如需确保覆盖已有版本，可加 `--force-reinstall`。 |
| **`pip install corecoder`**<br>（安装 PyPI 包） | **安装项目代码**：从 PyPI 下载 `corecoder` 的发布包，并将其安装到 `site-packages`。<br><br>**安装依赖包**：根据发布包中的依赖声明，将所需第三方依赖包安装到 `site-packages`。                                                                                                                     | 被复制到 Python 环境的 `site-packages` 目录中，例如 `C:\Python312\Lib\site-packages\corecoder`。 | **不生效，需要重新安装**：Python 只能从 `site-packages` 中的副本读取代码。你修改的是本地项目文件夹，但这个文件夹已经和 Python 环境无关了，所以修改会被忽略。             |

> 提示：可编辑安装的底层实现会随构建工具而变化；这里以 `.pth` 文件这一常见实现说明其行为。无论具体实现如何，其目的都是让 Python 导入本地源码。


## 依赖隔离管理

### Go Modules

Golang每个项目可以通过Go Modules机制通过go.mod来单独管理依赖，go.mod 文件声明自己的模块名和依赖。这个文件明确记录了项目需要哪些库以及它们的精确版本。缓存目录下使用多版本共存形式，项目A和项目B即便依赖同一个库的不同大版本，它们也各自从缓存中读取正确的版本，完全不会冲突。

### Python 虚拟环境

与Golang不同，因为历史遗留问题，Python它的包安装机制是全局且基于文件系统的，因此为了避免不同项目之间的依赖冲突，Python通常使用虚拟环境（venv）来隔离每个项目的依赖。通过创建独立的虚拟环境，每个项目可以拥有自己的 `site-packages` 目录，从而确保不同项目对同一库的版本需求不会互相干扰。

> 生产环境通常也使用独立的虚拟环境或容器运行项目，而不是直接依赖系统 Python 环境。

### Python 与 Go 的依赖隔离对比

| 性       | Python (pip + venv)                                       | Go (Go Modules)                                                          |
| :------- | :-------------------------------------------------------- | :----------------------------------------------------------------------- |
| 隔离方式 | 通过venv创建独立的 site-packages 目录树，隔离整个依赖集。 | 项目通过go.mod声明依赖，并从全局共享缓存中加载精确的版本，实现逻辑隔离。 |
| 依赖存储 | 存储在激活的虚拟环境的 site-packages 目录内。             | 全局缓存于 $GOPATH/pkg/mod，所有项目共享，但通过版本号区分。             |
| 根本目的 | 解决依赖冲突：防止不同项目对同一库的版本需求冲突。        | 实现版本锁定：确保在不同机器上构建的一致性，并天然支持多版本共存。       |

### 创建和使用虚拟环境
>注意在 VS Code 中需要`ctrl+p`输入`>`搜索python的解释器,并将 Python Interpreter 切换为虚拟环境文件夹中的 Python 解释器，而不是系统全局Interpreter。否则无法解析相关依赖包。
1. **在项目根目录下创建虚拟环境**（文件夹名为 `myenv`，可自定义）

```bash
python -m venv myenv
```

2. **激活虚拟环境**

修改你当前 CMD 会话的环境变量 是的后续的 Python 和 pip 命令都指向虚拟环境中的解释器和包管理器。
且使用myenv作为命令行前缀，方便区分当前是否在虚拟环境中。

**Windows**：

```powershell
myenv\Scripts\activate
```

**Mac / Linux**：

```bash
source myenv/bin/activate
```

3. **激活后，命令行前缀会变成 `(myenv)`**，表示你现在在虚拟环境里。

此时执行 `pip install`，包会安装到这个虚拟环境里，而不是系统 Python。

**确认自己是否在虚拟环境**：查看 Python 解释器的路径。

```bash
where python          # Windows
which python          # Mac/Linux
```

如果输出路径包含 `myenv`，说明你在虚拟环境里。

> 如果该项目使用了虚拟环境，那么项目所有的python操作均在虚拟环境中进行，包含启动，测试等。确保依赖隔离和版本一致性。

4. **退出虚拟环境**

```bash
deactivate
```

- **删除虚拟环境**
虚拟环境文件夹通常直接放在项目的根目录下，手动删除虚拟环境文件夹即可彻底删除虚拟环境。或者也可以用以下命令删除
**Windows (CMD)**
`rmdir /s myenv`    （`rmdir` 删除目录， `/s`递归删除）
**Windows (PowerShell)**
`Remove-Item -Recurse -Force myenv`
**Mac / Linux**
`rm -rf myenv`

## Python 包的初始化与模块入口

Python 中以双下划线开头和结尾的名称，例如 `__main__`，通常称为 dunder 名称。部分 dunder 名称由解释器或导入系统识别，因此拼写必须精确匹配。

### `__init__.py`

`__init__.py` 是常规 Python 包的初始化文件。执行 `import package_name` 时，Python 会执行该文件中的代码。

- 可以定义包版本等元数据，例如 `__version__`。`__version__` 是社区约定，不是解释器内置行为。
- 可以重新导出常用对象，使调用方使用更短的导入路径，例如 `from corecoder import Agent`。
- `__all__` 用于声明 `from package_name import *` 可导出的名称；它不限制普通的显式导入。
- Python 3.3 起可以创建不含 `__init__.py` 的命名空间包，但这类包没有可执行的初始化文件，也无法在其中集中声明公共 API。

将文件改名为 `_init_.py` 后，它会成为普通模块，导入包时不会自动执行。

### `__main__.py`

`__main__.py` 是包的模块启动入口。执行以下命令时，Python 会查找并运行包内这个精确名称的文件：

```bash
python -m package_name
```

例如，执行 `python -m corecoder` 时会运行 `corecoder/__main__.py`。CoreCoder 的该文件将控制权转交给 `corecoder.cli.main()`，因此模块启动方式与 CLI 主入口使用同一套逻辑。



# Python 中的类与对象

类和对象是 Python 面向对象编程的基础。类用于定义一组对象共有的数据和行为，对象是根据类创建出的具体实例。

## 类与对象的关系

可以将类理解为对象的定义或类型，将对象理解为类创建出的具体值。例如，`User` 是一个类，`user = User("Alice")` 创建出的 `user` 是 `User` 类的一个实例。

```python
class User:
    def __init__(self, name):
        self.name = name

    def greet(self):
        return f"Hello, {self.name}"


user = User("Alice")
print(user.greet())
```

上面的代码包含以下概念：

- `class User`：使用 Python 内置的 `class` 语法定义类。
- `__init__`：实例初始化方法。创建对象后，Python 会自动调用它为对象设置初始状态。
- `self`：指向当前实例的参数名。`self` 不是 Python 关键字，而是约定俗成的写法。
- `self.name`：当前实例的实例属性。
- `greet`：定义在类中的实例方法。
- `User("Alice")`：调用类创建一个 `User` 实例。

Python 中的类本身也是对象，类由元类创建；但在日常开发中，通常只需要先理解“类用于创建实例、实例保存具体状态并调用方法”这一层关系。

## 定义类的基本组成

一个类通常包含属性和方法：

```python
class Account:
    category = "personal"  # 类属性

    def __init__(self, owner, balance=0):
        self.owner = owner  # 实例属性
        self.balance = balance

    def deposit(self, amount):
        self.balance += amount

    def get_balance(self):
        return self.balance
```

### 实例属性与类属性

- **类属性**：所有对象共享的数据。属于类本身
- **实例属性**：每个对象独立拥有的数据，不同实例可以拥有不同的值。通常在 `__init__` 中通过 `self.attribute = value` 创建

```python
first = Account("Alice")
second = Account("Bob")

print(first.category)   # 可以通过实例读取类属性
print(Account.category) # 也可以通过类读取类属性
```

如果通过实例给同名属性赋值，通常会创建或修改该实例自己的属性，不会修改类属性：

```python
first.category = "business"
print(first.category)   # business
print(second.category)  # personal
print(Account.category) # personal
```

类属性适合保存所有实例共享的常量或配置。可变对象不应在类属性中作为每个实例的独立容器，否则多个实例可能意外共享同一份数据。

### 实例方法

实例方法的第一个参数通常命名为 `self`，用于访问当前实例的属性和其他实例方法：

```python
class Counter:
    def __init__(self):
        self.value = 0

    def increase(self):
        self.value += 1


counter = Counter()
counter.increase()
```

调用 `counter.increase()` 时，Python 会自动将 `counter` 作为第一个参数传给 `increase`，因此它等价于 `Counter.increase(counter)`。

### 类方法与静态方法

类方法和静态方法仍然定义在类中，但它们接收的参数和使用场景不同：

```python
class User:
    user_count = 0

    def __init__(self, name):
        self.name = name
        User.user_count += 1

    @classmethod
    def from_text(cls, text):
        return cls(text.strip())

    @staticmethod
    def is_valid_name(name):
        return bool(name.strip())
```

- **实例方法**：第一个参数通常是 `self`，可以访问当前实例的状态。
- **类方法**：使用 `@classmethod`，第一个参数通常是 `cls`，表示当前类。它可以访问类属性，也可以调用 `cls(...)` 创建实例，常用于工厂方法或替代构造方法。
- **静态方法**：使用 `@staticmethod`，不会自动接收 `self` 或 `cls`。它只是逻辑上属于这个类，但不依赖实例状态或类状态的普通函数。

```python
user = User.from_text(" Alice ")
print(User.is_valid_name("Alice"))
```

## 对象的创建、访问与生命周期

```python
user = User("Alice")
user.name = "Bob"       # 修改实例属性
print(user.name)        # 读取实例属性
del user.name           # 删除实例属性
```

访问属性时，Python 通常会先查找实例本身，再查找实例所属的类及其父类。实例可以通过 `obj.attribute` 访问属性，也可以通过 `obj.method()` 调用方法。

当一个对象不再被引用时，Python 的垃圾回收机制可能回收它占用的内存。开发者通常不需要像 Go 中那样手动释放普通对象；但文件、网络连接等外部资源仍应通过上下文管理器或显式关闭方法及时释放。

## 继承与方法重写

子类可以继承父类的属性和方法，并通过方法重写提供更具体的实现：

```python
class Animal:
    def speak(self):
        return "sound"


class Dog(Animal):
    def speak(self):
        return "bark"

    def describe(self):
        return f"Dog: {super().speak()}"
```

- `class Dog(Animal)`：定义 `Dog` 继承 `Animal`。
- `speak`：子类重写父类同名方法。
- `super()`：访问父类实现，避免重复写出父类名称。

Python 支持多继承，但多个父类之间的初始化顺序和方法查找顺序可能增加理解成本。除非确实需要复用多个独立的行为，否则优先使用单继承或组合。

Go 中通常使用 `struct` 保存数据、使用方法绑定行为，并通过接口实现抽象；Python 的类则可以同时包含数据、方法、继承关系和运行时行为。Python 类与 Go 的 `struct` 有相似之处，但不能简单地认为二者完全等价。

## 抽象基类与 Protocol

抽象基类（Abstract Base Class，简称 ABC）和 `Protocol` 都可以用来描述一组对象需要提供的操作，但它们表达约束的方式不同：

- **抽象基类**：通过显式继承建立关系，并可以在运行时检查子类是否实现了抽象方法。
- **`Protocol`**：通过对象实际提供的方法和属性建立关系，主要由静态类型检查工具判断是否兼容，不要求实现类显式继承协议。

### 抽象基类

抽象基类适合定义由项目内部控制的扩展层次，并可以放置多个子类共享的实现：

```python
from abc import ABC, abstractmethod


class Storage(ABC):
    @abstractmethod
    def save(self, data):
        pass


class FileStorage(Storage):
    def save(self, data):
        print(f"save: {data}")
```

`ABC` 是标准库 `abc` 模块提供的抽象基类。`@abstractmethod` 将方法声明为抽象方法。没有实现所有抽象方法的子类不能正常实例化，因此可以在运行时较早发现实现不完整的问题。

抽象基类还可以提供普通方法、保存共享状态，并要求子类复用某些公共流程。例如，项目内部拥有多个存储实现，并且希望所有实现都遵循统一的初始化流程时，抽象基类通常更合适。

### Protocol

`Protocol` 来自标准库 `typing`，使用结构化类型（也称为鸭子类型的静态版本）描述对象需要具备的接口：

```python
from typing import Protocol


class Writable(Protocol):
    def write(self, data: str) -> None:
        ...


def save_message(target: Writable, message: str) -> None:
    target.write(message)


class FileWriter:
    def write(self, data: str) -> None:
        print(data)


save_message(FileWriter(), "hello")
```

`FileWriter` 没有继承 `Writable`，但它提供了协议要求的 `write` 方法，因此静态类型检查工具会认为它可以传给 `save_message`。这种方式不会强制外部实现依赖当前项目的基类，适合让已有类、第三方类或测试替身接入自己的代码。

`Protocol` 的主要约束来自静态类型检查工具，例如 mypy 或 pyright。普通的 `Protocol` 不会自动在运行时检查对象是否满足协议；

### 应用场景与选型

`Protocol` 只要求实现方提供所需的结构不需要必须子类显示继承方法，所以**适合“调用方需要什么能力”的接口设计**
`抽象基类ABC`需要子类显示继承方法，**通常适合“扩展方必须继承并遵循什么基类契约”的设计。**


| 特性                   | **ABC（抽象基类）**                    | **Protocol（协议）**                         |
| :--------------------- | :------------------------------------- | :------------------------------------------- |
| **实现方式**           | **名义子类型**：必须显式继承           | **结构子类型**：无需继承，只要求方法签名匹配 |
| **契约检查时机**       | **运行时**（实例化时抛出 `TypeError`） | **静态检查时**（依赖 mypy / pyright 等工具） |
| **对第三方类的友好度** | **低**：无法约束未继承该 ABC 的类      | **高**：可无缝适配任何形状匹配的类           |
| **代码复用能力**       | **强**：可提供具体方法实现供子类复用   | **弱**：主要用于定义接口形状，不提供实现     |
| **默认适用场景**       | 内部接口、需要共享实现、强运行时约束   | 外部 / 第三方接口、库设计、轻量级类型契约    |



## 类常用装饰器
###  装饰器的基本概念

装饰器是接收一个函数、方法或类，并返回修改后对象的可调用对象。下面两种写法等价：

```python
@decorator
def work():
    pass
```

```python
def work():
    pass


work = decorator(work)
```

`@decorator` 是 Python 的装饰器语法。它不是装饰器本身，而是对“调用装饰器并重新绑定名称”的简写。

类中常见的装饰器主要分为两类：

- **方法装饰器**：修改类中方法的绑定方式，例如 `@property`、`@classmethod` 和 `@staticmethod`。
- **类装饰器**：接收整个类并修改或增强类，例如 `@dataclass`。
### `@property`

`@property` 将一个方法转换为只读属性的访问形式。调用方不需要写括号，但读取时仍会执行方法中的逻辑：

```python
class User:
    def __init__(self, name):
        self._name = name

    @property
    def name(self):
        return self._name
```

```python
user = User("Alice")
print(user.name)  # 不写 user.name()
```

属性名通常使用下划线版本保存实际数据，例如 `_name`，以避免 `name` 属性与 `name()` 方法发生递归访问。

可以使用 `@属性名.setter` 增加赋值时的校验逻辑：

```python
class User:
    def __init__(self, name):
        self.name = name

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, value):
        if not value.strip():
            raise ValueError("name cannot be empty")
        self._name = value.strip()
```

`@property` 适合在保持 `obj.value` 调用形式不变的同时，增加计算、校验或封装逻辑。

### `@classmethod`

`@classmethod` 将方法绑定到类，而不是绑定到某个实例。方法的第一个参数通常是 `cls`：

```python
class User:
    def __init__(self, name):
        self.name = name

    @classmethod
    def guest(cls):
        return cls("Guest")
```

```python
user = User.guest()
```

常见用途是定义工厂方法或替代构造入口。当子类继承 `guest` 时，`cls("Guest")` 会尝试创建子类实例，因此比直接写死 `User("Guest")` 更适合继承场景。

### `@staticmethod`

`@staticmethod` 将函数放在类的命名空间中，但不自动传入实例或类：

```python
class User:
    @staticmethod
    def is_valid_name(name):
        return bool(name and name.strip())
```

```python
User.is_valid_name("Alice")
```

如果一个函数既不需要访问 `self`，也不需要访问 `cls`，但在概念上属于某个类，可以使用静态方法。如果函数与这个类没有明显的概念关联，则通常应定义为模块级函数。

### `@abstractmethod`

`@abstractmethod` 用于声明子类必须实现的方法，通常与 `ABC` 一起使用：

```python
from abc import ABC, abstractmethod


class Parser(ABC):
    @abstractmethod
    def parse(self, text):
        pass
```

抽象方法主要用于定义规范，而不是提供完整实现。具体子类实现所有抽象方法后，才能创建实例。

### `@dataclass`

`@dataclass` 是标准库 `dataclasses` 提供的类装饰器，适合主要用于保存数据的类。它会根据字段定义自动生成部分常用方法，例如 `__init__`、`__repr__` 和 `__eq__`：

```python
from dataclasses import dataclass


@dataclass
class User:
    name: str
    age: int = 0


user = User("Alice", 18)
print(user)  # User(name='Alice', age=18)
```

`@dataclass` 不会把所有业务逻辑自动添加到类中，也不会替代类方法的设计。它主要减少数据类中重复的初始化、显示和比较代码。

如果字段的默认值是可变对象，例如列表，应使用 `field(default_factory=list)`，使每个实例获得独立的列表：

```python
from dataclasses import dataclass, field


@dataclass
class TaskList:
    tasks: list[str] = field(default_factory=list)
```

## 常用选择

| 需求                             | 常用方式                  |
| :------------------------------- | :------------------------ |
| 保存数据并自动生成基础方法       | `@dataclass`              |
| 将方法以属性形式访问             | `@property`               |
| 创建类级别的替代构造入口         | `@classmethod`            |
| 定义不依赖实例或类状态的辅助逻辑 | `@staticmethod`           |
| 强制子类实现某个方法             | `ABC` + `@abstractmethod` |
| 只需要模块级辅助函数             | 普通函数，而不是静态方法  |

这些装饰器都来自 Python 内置功能或标准库：`property`、`classmethod` 和 `staticmethod` 是内置对象，`ABC`、`abstractmethod` 和 `dataclass` 分别来自标准库 `abc` 与 `dataclasses`。项目代码也可以定义自定义装饰器，但自定义装饰器属于项目代码，不是 Python 内置功能。

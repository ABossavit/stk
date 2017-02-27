"""
Defines the Population class.

"""

import itertools as it
import os
import numpy as np
import pickle
from collections import Counter
import json

from .fitness import _calc_fitness, _calc_fitness_serial
from .plotting import plot_counter
from .ga_tools import GATools
from ..convenience_tools import dedupe
from ..molecular import (MacroMolecule, Cage, Molecule,
                               StructUnit, StructUnit2, StructUnit3)
from ..molecular.optimization.optimization import (_optimize_all,
                                        _optimize_all_serial)

class Population:
    """
    A container for instances of ``MacroMolecule`` and ``Population``.

    This is the central class of MMEA. The GA is invoked by calling the
    ``gen_offspring``, ``gen_mutants`` and ``select`` methods of this
    class on a given instance. However, this class is a container of
    ``MacroMolecule`` and other ``Population`` instances first and
    foremost. It delegates GA operations to its `ga_tools` attribute.
    Any functionality related to the GA should be delegated to this
    attribute. The ``gen_offspring`` and ``gen_mutants`` methods can
    serve as a guide to how this should be done. A comphrehensive
    account of how the interaction between these two classes is
    provided in the developer's guide.

    For consistency and maintainability, collections of
    ``MacroMolecule`` or ``Population`` instances should always be
    placed in a ``Population`` instance. As a result, any function
    which should return multiple ``MacroMolecule`` or ``Population``
    instances can be expected to return a single ``Population``
    instance holding the desired instances. Some functions may have to
    return the population organized in a specific way, depending on
    use.

    The only operations directly addressed by this class and definined
    within it are those relevant to its role as a container. It
    supports all expected and necessary container operations such as
    iteration, indexing, membership checks (via the ``is in`` operator)
    as would be expected. Additional operations such as comparison via
    the ``==``, ``>``, etc. operators is also supported. Details of the
    various implementations and a full list of supported operations can
    be found by examining the included methods. Note that all
    comparison operations are accounted for with the ``total_ordering``
    decorator, even if they are not explicity defined.

    Attributes
    ----------
    populations : list of ``Population`` instances
        A list of other instances of the ``Population`` class. This
        allows the implementation of subpopulations or evolutionary
        islands. This attribute is also used for grouping
        macromolecules within a given population for organizational
        purposes if need be.

    members : list of ``MacroMolecule`` instances
        A list of ``MacroMolecule`` instances. These are the members of
        the population which are not held within any subpopulations.
        This means that not all members of a population are stored
        here. To access all members of a population the generator
        method ``all_members`` should be used.

    ga_tools : GATools, optional
        An instance of the ``GATools`` class. Calls to preform GA
        operations on the ``Population`` instance are delegated
        to this attribute.

    """

    def __init__(self, *args):
        """
        Initializer for ``Population`` class.

        This initializer creates a new population from the
        ``Population`` and ``MacroMolecule`` instances given as
        arguments. It also accepts a ``GATools`` instance if the
        population is to have GA operations performed on it.

        The arguments can be provided in any order regardless of type.

        Parameters
        ----------
        *args : MacroMolecule, Population, GATools
            A population is initialized with as many ``MacroMolecule``
            or ``Population`` arguments as required. These are placed
            into the `members` or `populations` attributes,
            respectively. A ``GATools`` instance may be included
            and will be placed into the `ga_tools` attribute.

        Raises
        ------
        TypeError
            If the instance is initialized with something other than
            ``MacroMolecule``, ``Population`` or ``GATools`` object.

        """

        # Generate `populations`, `members` and `ga_tools` attributes.
        self.populations = []
        self.members = []
        self.ga_tools = GATools.init_empty()

        # Determine type of supplied arguments and place in the
        # appropriate attribute.  ``Population`` types added to
        # `populations` attribute, ``MacroMolecule`` into `members` and
        # if ``GATools`` is supplied it is placed into `ga_tools`.
        # Raise a ``TypeError``  if an argument was not ``GATools``,
        # ``MacroMolecule`` or ``Population`` type.
        for arg in args:
            if isinstance(arg, Population):
                self.populations.append(arg)
                continue

            if isinstance(arg, MacroMolecule):
                self.members.append(arg)
                continue

            if isinstance(arg, GATools):
                self.ga_tools = arg
                continue

            raise TypeError(
                    ("Population can only be"
                     " initialized with ``Population``,"
                     " ``MacroMolecule`` and ``GATools`` types."), arg)

    @classmethod
    def init_cage_isomers(cls, lk_file, bb_file, topology, ga_tools,
                          lk_fg=None, bb_fg=None):
        """
        Creates a population holding all structural isomers of a cage.

        Structural isomers here means that the building blocks are
        rotated in position so that every possible bond combination
        with linkers is formed.

        Parameters
        ----------
        lk_file : str
            The full path of the file holding the linker of the cage.

        bb_file : str
            The full path to the file holding the building block of the
            cage.

        topology : _CageTopology child class
            An object of the topology to be made.

        ga_tools : GATools
            The GATools instance to be used by created population.

        lk_fg : str (default = None)
            The name of the linker's functional group. If ``None`` then
            `lk_file` is checked for a name.

        bb_fg : str (default = None)
            The name of the building block's functional group. If
            ``None`` then `bb_file` is checked for a name.

        Returns
        -------
        Population
            A population filled with isomers of a cage.

        """

        n = len(topology.positions_A)
        alignments = set()

        for x in it.combinations_with_replacement([0,1,2], n):
            for y in it.permutations(x, n):
                alignments.add(y)

        lk = StructUnit(lk_file, lk_fg)
        if len(lk.functional_group_atoms()) > 2:
            lk = StructUnit3(lk_file, lk_fg)
        else:
            lk = StructUnit2(lk_file, lk_fg)

        bb = StructUnit3(bb_file, bb_fg)

        pop = cls(ga_tools)
        for i, align in enumerate(alignments):
            topology.alignment = align
            cage = Cage((lk, bb), topology)
            pop.members.append(cage)

        return pop

    @classmethod
    def init_random_cages(cls, bb_db, lk_db,
                          topologies, size, ga_tools,
                          bb_fg=None, lk_fg=None):
        """
        Creates a population of cages built from provided databases.

        All cages are held in the population's `members` attribute.

        From the supplied databases a random linker and building-block*
        molecule is selected to form a cage. This is done until `size`
        cages have been formed. After this, all of them are returned
        together in a ``Population`` instance.

        Parameters
        ----------
        bb_db : str
            The full path of the database of building-block* molecules.

        lk_db : str
            The full path of the database of linker molecules.

        topolgies : iterable of ``Topology`` child classes
            An iterable holding topologies which should be randomly
            selected for cage initialization.

        size : int
            The size of the population to be initialized.

        ga_tools : GATools
            The GATools instance to be used by created population.

        bb_fg : str (default = None)
            The name of the functional group present in molecules in
            `bb_db`. It is the name of the functional group used to
            build the macromolecules. If ``None`` it is assumed that
            the name is present in `bb_db`.

        lk_fg : str (default = None)
            The name of the functional group present in molecules in
            `lk_db`. It is the name of the functional group used to
            build the macromolecules. If ``None`` it is assumed that
            the name is present in `lk_db`.

        Returns
        -------
        Population
            A population filled with random cages.

        """

        pop = cls(ga_tools)
        for x in range(size):
            topology = np.random.choice(topologies)
            # Make a building block.
            while True:
                try:
                    bb_file = np.random.choice(os.listdir(bb_db))
                    bb_file = os.path.join(bb_db, bb_file)
                    bb = StructUnit3(bb_file, bb_fg)
                    break

                except TypeError:
                    continue

            # Make a linker.
            while True:
                try:
                    lk_file = np.random.choice(os.listdir(lk_db))
                    lk_file = os.path.join(lk_db, lk_file)
                    lk = StructUnit(lk_file, lk_fg)

                    if len(lk.bonder_ids) >= 3:
                        lk = StructUnit3(lk_file, lk_fg)
                    else:
                        lk = StructUnit2(lk_file, lk_fg)

                    break

                except TypeError:
                    continue
                    
            pop.members.append(Cage({bb, lk}, topology))

        return pop

    def add_members(self, population, duplicates=False):
        """
        Adds ``MacroMolecule`` instances into `members`.

        The ``MacroMolecule`` instances held within the supplied
        ``Population`` instance, `population`, are added into the
        `members` attribute of `self`. The supplied `population` itself
        is not added. This means that any information the `population`
        instance had about subpopulations is lost. This is because all
        of its ``MacroMolecule`` instances are added into the `members`
        attribute, regardless of which subpopulation they were
        originally in.

        The `duplicates` parameter indicates whether multiple instances
        of the same macromolecule are allowed to be added into the
        population. Note that the sameness of a macromolecule is judged
        by the `same` method of the ``MacroMolecule`` class, which is
        invoked by the ``in`` operator within this method. See the
        `__contains__` method of the ``Population`` class for details
        on how the ``in`` operator uses the `same` method.

        Parameters
        ----------
        population : Population (or iterable of ``MacroMolecule``s)
            ``MacroMolecule`` instances to be added to the `members`
            attribute and/or ``Population`` instances who's members, as
            generated by `all_members`, will be added to the `members`
            attribute.

        duplicates : bool (default = False)
            When ``False`` only macromolecules which are not already
            held by the population will be added. ``True`` allows more
            than one instance of the same macromolecule to be added.
            Whether two macromolecules are the same is defined by the
            `same` method of the ``MacroMolecule`` class.

        Modifies
        --------
        members
            Adds instances into the `members` attribute of `self`.

        Returns
        -------
        None : NoneType

        """

        if duplicates:
            self.members.extend(mol for mol in population)
        else:
            self.members.extend(mol for mol in population
                                                    if mol not in self)
    def add_subpopulation(self, population):
        """
        Appends a population into the `populations` attribute.

        Parameters
        ----------
        population : Population
            The population to be added as a subpopulation.

        Modifies
        --------
        populations : list of Populations
            The `populations` attribute of `self` has ``Population``
            instaces added to it.

        Returns
        -------
        None : NoneType

        """

        self.populations.append(population)

    def all_members(self):
        """
        Yields all members in the population and its subpopulations.

        Yields
        ------
        MacroMolecule
            The next ``MacroMolecule`` instance held within the
            population or its subpopulations.

        """

        # Go through `members` attribute and yield ``MacroMolecule``
        # instances held within one by one.
        for ind in self.members:
            yield ind

        # Go thorugh `populations` attribute and for each
        # ``Population`` instance within, yield ``MacroMolecule``
        # instances from its `all_members` generator.
        for pop in self.populations:
            yield from pop.all_members()

    def calculate_member_fitness(self):
        """
        Applies the fitness function on all members.

        Returns
        -------
        list
            The a copy of the members in `self` with the fitness
            calculated.


        """

        return _calc_fitness(self.ga_tools.fitness, self)

    def dump(self, path):
        """
        Write the population to a file.

        The population is written in the JSON format in the following
        way. The population is represented as a list,

            [mem1.json(), mem2.json(), [mem3.json(), [mem4.json()]]]

        where each member of the population held directly in the
        `members` attribute is placed an an element in the list. Any
        subpopulations are held as  sublists.

        Parameters
        ----------
        path : str
            The full path of the file to which the population should
            be written.

        Returns
        -------
        None : NoneType

        """

        with open(path, 'w') as f:
            json.dump(self.tolist(), f, indent=4)

    def exit(self):
        """
        Checks the if the exit criterion has been satisfied.

        Returns
        -------
        bool
            ``True`` if the exit criterion is satisfied, else
            ``False``.

        """

        return self.ga_tools.exit(self)

    @classmethod
    def fromlist(cls, pop_list, load_names=True):
        """
        Initializes a population from a list representation of one.

        Parameters
        ----------
        pop_list : list of str and lists
            A list which represents a population. Like the ones created
            by `tolist()`.

        load_names : bool (default = True)
            If ``True`` then the `name` attribute stored in the JSON
            objects is loaded. If ``False`` then it's not.

        Returns
        -------
        Population
            The population represented by `pop_list`.

        """

        pop = cls()
        for item in pop_list:
            if isinstance(item, dict):
                pop.members.append(Molecule.fromdict(item, load_names))
            elif isinstance(item, list):
                pop.populations.append(cls.fromlist(item, load_names))

            else:
                raise TypeError(('Population list must consist only'
                                 ' of strings and lists.'))
        return pop

    def gen_mutants(self, counter_name='mutation_counter.png'):
        """
        Returns a population of mutant ``MacroMolecule`` instances.

        This is a GA operation and as a result this method merely
        delegates the request to the ``Mutation`` instance held in the
        `ga_tools` attribute.

        Parameters
        ----------
        counter_name : str (default='mutation_counter.png')
            The name of the .png file showing which members were
            selected for mutation.

        Returns
        -------
        Population
            A population holding mutants created by mutating contained
            ``MacroMolecule`` instances.

        """

        return self.ga_tools.mutation(self, counter_name)

    def gen_next_gen(self, pop_size, counter_name='gen_select.png'):
        """
        Returns a population hodling the next generation of structures.

        This function also creates a .png plot of the selection
        distribution.

        Parameters
        ----------
        pop_size : int
            The size of the next generation.

        counter_name : str (default='gen_select.png')
            The name of the .png file showing which members were
            selected for the next generation.

        Returns
        -------
        Population
            A population holding the next generation of individuals.

        """

        new_gen = Population(self.ga_tools)
        counter = Counter()
        for member in self.select('generational'):
            counter.update([member])
            new_gen.add_members([member])
            if len(new_gen) == pop_size:
                break
        for member in self:
            if member not in counter.keys():
                counter.update({member : 0})

        plot_counter(counter, counter_name)
        return new_gen

    def gen_offspring(self, counter_name='crossover_counter.png'):
        """
        Returns a population of offspring ``MacroMolecule`` instances.

        This is a GA operation and as a result this method merely
        delegates the request to the ``Crossover`` instance held in the
        `ga_tools` attribute. The ``Crossover`` instance takes care of
        selecting parents and combining them to form offspring. The
        ``Crossover`` instance delegates the selection to the
        ``Selection`` instance as would be expected. The request to
        perform crossovers is done by calling the ``Crossover``
        instance with the population as the argument. Calling of the
        ``Crossover``instance returns a ``Population`` instance holding
        the generated offspring. All details regarding the crossover
        procedure are handled by the ``Crossover`` instance.

        For more details about how crossover is implemented see the
        ``Crossover`` class documentation.

        Parameters
        ----------
        counter_name : str (default='crossover_counter.png')
            The name of the .png file showing which members were
            selected for crossover.

        Returns
        -------
        Population
            A population holding offspring created by crossing
            contained the ``MacroMolecule`` instances.

        """

        return self.ga_tools.crossover(self, counter_name)

    @classmethod
    def load(cls, path, ga_tools=None, load_names=True):
        """
        Initializes a Population from one dumped to a file.

        Parameters
        ----------
        path : str
            The full path of the file holding the dumped population.

        ga_tools : GATools (default = None)
            A GATools instance to be used by the loaded population. If
            ``None`` the ``GATools`` instance of the loaded population
            is used.

        load_names : bool (default = True)
            If ``True`` then the `name` attribute stored in the JSON
            objects is loaded. If ``False`` then it's not.

        Returns
        -------
        Population
            The population stored in the dump file.

        """

        with open(path, 'r') as f:
            pop_list = json.load(f)

        pop = cls.fromlist(pop_list, load_names)
        pop.ga_tools = ga_tools
        return pop

    def max(self, key):
        """
        Calculates the max given a key.

        This method applies key(member) on every member of the
        population and returns the max of returned values.

        For example, if the max value of the attribute `cavity_size`
        was desired:

            population.max(
                 lambda macro_mol : macro_mol.topology.cavity_size())

        Parameters
        ----------
        key : function
            A function which should take a MacroMolecule instance as
            its argument and return a value.

        Returns
        -------
        float
            The max of the values returned by the function `key` when
            its applied to all members of the population.

        """

        return np.max([key(member) for member in self], axis=0)

    def mean(self, key):
        """
        Calculates the mean given a key.

        This method applies key(member) on every member of the
        population and returns the mean of returned values.

        For example, if the mean value of the attribute `cavity_size`
        was desired:

            population.mean(
                 lambda macro_mol : macro_mol.topology.cavity_size())

        Parameters
        ----------
        key : function
            A function which should take a MacroMolecule instance as
            its argument and return a value.

        Returns
        -------
        float
            The mean of the values returned by the function `key` when
            its applied to all members of the population.

        """

        return np.mean([key(member) for member in self], axis=0)

    def min(self, key):
        """
        Calculates the min given a key.

        This method applies key(member) on every member of the
        population and returns the min of returned values.

        For example, if the min value of the attribute `cavity_size`
        was desired:

            population.min(
                 lambda macro_mol : macro_mol.topology.cavity_size())

        Parameters
        ----------
        key : function
            A function which should take a MacroMolecule instance as
            its argument and return a value.

        Returns
        -------
        float
            The min of the values returned by the function `key` when
            its applied to all members of the population.

        """

        return np.min([key(member) for member in self], axis=0)

    def normalize_fitness_values(self):
        """
        Applies the normalization function.

        Returns
        -------
        None : NoneType

        """

        return self.ga_tools.normalization(self)

    def optimize_population(self):
        """
        Optimizes all the members of the population.

        This function should invoke either the ``optimize_all()`` or
        ``optimize_all_serial()`` functions. ``optimize_all()``
        optimizes all members of the population in parallel.
        ``optimize_all_serial()`` does them serially. Probably best not
        to use the serial version unless debugging.

        The parallel optimization creates cloned instances of the
        population's members. It is these that are optimized. This
        means that the ``.mol`` files are changed but any instance
        attributes are not. See ``optimize_all()`` function
        documentation in ``optimization.py`` for more details.

        Modifies
        --------
        MacroMolecule
            This function replaces the pristine rdkit molecule
            instances with optimizes versions. It also replaces the
            content of the pristine ``.mol`` files with pristine
            structures.

        Returns
        -------
        iterator of MacroMolecule objects
            If a parallel optimization was chosen, this iterator yields
            the ``MacroMolecule`` objects that have had their
            attributes changed as a result of the optimization. They
            are modified clones of the original population's
            macromolecules.

            If a serial optimization is done the iterator does not yield
            clones.

        """

        return _optimize_all(self.ga_tools.optimization, self)

    def remove_duplicates(self, between_subpops=True, top_seen=None):
        """
        Removes duplicates from a population and preserves structure.

        The question of which ``MacroMolecule`` instance is preserved
        from a choice of two is difficult to answer. The iteration
        through a population is depth-first, so a rule such as ``the
        macromolecule in the topmost population is preserved`` is not
        the case here. Rather, the first ``MacroMolecule`` instance
        iterated through is preserved.

        However, this question is only relevant if duplicates in
        different subpopulations are being removed. In this case it is
        assumed that it is more important to have a single instance
        than to worry about which subpopulation it is in.

        If the duplicates are being removed from within subpopulations,
        each subpopulation will end up with a single instance of all
        macromolecules held before. There is no ``choice``.

        Parameters
        ----------
        between_subpops : bool (default = False)
            When ``False`` duplicates are only removed from within a
            given subpopulation. If ``True`` all duplicates are
            removed, regardless of which subpopulation they are in.

        Modifies
        --------
        members
            Duplicate instances are removed from the `members`
            attribute of the population or subpopulations.

        Returns
        -------
        None : NoneType

        """

        # Whether duplicates are being removed from within a single
        # subpopulation or from different subpopulations, the duplicate
        # must be removed from the `members` attribute of some
        # ``Population`` instance. This means ``dedupe`` can be run
        # on the `members` attribute of every population or
        # subpopulation. The only difference is that when duplicates
        # must be removed between different subpopulations a global
        # ``seen`` set must be defined for the entire top level
        # ``Population`` instance. This can be passed each time dedupe
        # is being called on a subpopulation's `members` attribute.
        if between_subpops:
            if top_seen is None:
                seen = set()
            if type(top_seen) == set:
                seen = top_seen

            self.members = list(dedupe(self.members, seen=seen))
            for subpop in self.populations:
                subpop.remove_duplicates(between_subpops,
                                             top_seen=seen)

        # If duplicates are only removed from within the same
        # subpopulation, only the `members` attribute of each
        # subpopulation needs to be cleared of duplicates. To do this,
        # each `members` attribute is deduped recursively.
        if not between_subpops:
            self.members = list(dedupe(self.members))
            for subpop in self.populations:
                subpop.remove_duplicates(between_subpops=False)

    def remove_failures(self):
        """
        Removes all members where `failed` is ``True``.

        The structure of the population is preserved.

        Modifies
        --------
        self : Population
            All members of the population which have their `failed`
            attribute set to ``True`` are removed.

        Returns
        -------
        None : NoneType

        """

        self.members = [ind for ind in self.members if not ind.failed]
        for subpop in self.populations:
            subpop.remove_failures()

    def select(self, type_='generational'):
        """
        Returns a generator field yielding selected members of `self`.

        Selection is a GA procedure and as a result this method merely
        delegates the selection request to the ``Selection`` instance
        held within the `ga_tools` attribute. The ``Selection``
        instance then returns a generator which yields
        ``MacroMolecule`` instances held within the population. Which
        macromolecules are yielded depends on the selection algorithm
        which was chosen during initialization and when calling this
        method. The selection instance (`self.ga_tools.selection`)
        returns the generator when it is called. See ``Selection``
        class documentation for more information.

        Because selection is required in a number of different ways,
        such as selecting the parents, ``MacroMolecule`` instances for
        mutation and ``MacroMolecule`` instances for the next
        generation, the type of selection must be specificed with the
        `type_` parameter. The valid values for `type_` will correspond
        to one of the attribute names of the ``Selection`` instance.

        For example, if `type_` is set to 'crossover' a selection
        algorithm which yields a parents will be invoked. If the
        `type_` is set to 'generational' an algorithm which yields the
        next generation will be invoked.

        The information regarding which generational, parent pool, etc.
        algorithm is used is held by the ``Selection`` instance. This
        method merely requests that the ``Selection`` instance performs
        the selection algorithm of the relevant type. The ``Selection``
        instance takes care of all the details to do with selection.

        Parameters
        ----------
        type_ : str (default = 'generational')
            A string specifying the type of selection to be performed.
            Valid values will correspond to names of attributes of the
            ``Selection`` class. Check ``Selection`` class
            documentation for details.

            Valid values include:
                'generational' - selects the next generation
                'crossover' - selects parents
                'mutation' - selects ``MacroMolecule`` instances for
                             mutation

        Returns
        -------
        generator
           A generator which yields ``MacroMolecule`` instances or
           tuples of them. Which instances are yielded depends on the
           selection algorithm used by the generator. This will depend
           on the `type_` provided.

        """

        return self.ga_tools.selection(self, type_)

    def tolist(self):
        """
        Converts the population to a list representation.

        The population and any subpopulations are represented as lists
        (and sublists), while members are represented by their JSON
        dictionaries (as strings).

        Returns
        -------
        str
            A JSON string representing the population.

        """

        pop = [x.json() for x in self.members]
        for sp in self.populations:
            pop.append(sp.tolist())
        return pop

    def write(self, dir_path, use_name=False):
        """
        Writes the ``.mol`` files of members to a directory.

        Parameters
        ----------
        dir_path : str
            The full path of the directory into which the ``.mol`` file
            is written.

        use_name : bool (default = False)
            When ``True`` the `name` attribute of the population's
            members is used to make the name of the .mol file. If
            ``False`` the files are just named after the member's
            index in the population.

        Returns
        -------
        None : NoneType

        """

        # If the directory does not exist, create it.
        if not os.path.exists(dir_path):
            os.mkdir(dir_path)

        for i, member in enumerate(self):
            if use_name:
                fname = os.path.join(dir_path, '{}.mol'.format(
                                                        member.name))
            else:
                fname = os.path.join(dir_path, '{}.mol'.format(i))

            member.write(fname)

    def __iter__(self):
        """
        Allows the use of ``for`` loops, ``*`` and ``iter`` function.

        When ``Population`` instances are iterated through they yield
        ``MacroMolecule`` instances generated by the `all_members`
        method. It also means that a ``Population`` instance can be
        unpacked with the ``*`` operator. This will produce the
        ``MacroMolecule`` instances yielded by the `all_members`
        method.

        Returns
        -------
        Generator
            The `all_members` generator. See `all_members` method
            documentation for more information.

        """

        return self.all_members()

    def __getitem__(self, key):
        """
        Allows the use of ``[]`` operator.

        Macromolecules held by the ``Population`` instance can be
        accesed by their index. Slices are also supported. These return
        a new ``Population`` instance holding the ``MacroMolecule``
        instances with the requested indices. Using slices will return
        a flat ``Population`` instance meaing no subpopulation
        information is preserved. All of the ``MacroMolecule``
        instances are placed into the `members` attribute of the
        returned ``Population`` instance.

        The index corresponds to the ``MacroMolecule`` yielded by the
        `all_members` method.

        This can be exploited if one desired to remove all
        subpopulations and transfer all the ``MacroMolecules``
        instances into the members attribute. For example,

        >>> pop2 = pop[:]

        ``pop2`` is a ``Population`` instance with all the same
        ``MacroMolecule`` instances as ``pop``, however all
        ``MacroMolecule`` instances are held within its `members`
        attribute and its `populations` attribute is empty. This may or
        may not be the case for the ``pop`` instance.

        Parameters
        ----------
        key : int, slice
            An int or slice can be used depending on if a single
            ``MacroMolecule`` instance needs to be returned or a
            collection of ``MacroMolecule`` instances.

        Returns
        -------
        MacroMolecule
            If the supplied `key` is an ``int``. Returns the
            ``MacroMolecule`` instance with the corresponding index
            from the `all_members` generator.

        Population
            If the supplied `key` is a ``slice``. The returned
            ``Population`` instance holds ``MacroMolecule`` instances
            in its `members` attribute. The ``MacroMolecule`` instances
            correspond to indices defined by the slice. The slice is
            implemented on the `all_members` generator.

        Raises
        ------
        TypeError
            If the supplied `key` is not an ``int`` or ``slice`` type.

        """

        # Determine if provided key was an ``int`` or a ``slice``.
        # If ``int``, return the corresponding ``MacroMolecule``
        # instance from the `all_members` generator.
        if isinstance(key, int):
            return list(self.all_members())[key]

        # If ``slice`` return a ``Population`` of the corresponding
        # ``MacroMolecule`` instances. The returned ``Population`` will
        # have the same `ga_tools` attribute as original ``Population``
        # instance.
        if isinstance(key, slice):
            mols = it.islice(self.all_members(),
                                     key.start, key.stop, key.step)
            pop = Population(*mols)
            pop.ga_tools = self.ga_tools
            return pop

        # If `key` is not ``int`` or ``slice`` raise ``TypeError``.
        raise TypeError("Index must be an integer or slice, not"
                        " {}.".format(type(key).__name__))

    def __len__(self):
        """
        Returns the number of members yielded by `all_members`.

        Returns
        -------
        int
            The number of members held by the population, including
            those held within its subpopulations.

        """

        return len(list(self.all_members()))

    def __sub__(self, other):
        """
        Allows use of the ``-`` operator.

        Subtracting one from another,

            pop3 = pop1 - pop2,

        returns a new population, pop3. The returned population
        contains all the ``MacroMolecule`` instances in pop1 except
        those also in pop2. This refers to all of the ``MacroMolecule``
        instances, including those held within any subpopulations. The
        returned population is flat. This means all information about
        subpopulations in pop1 is lost as all the ``MacroMolecule``
        instances are held in the `members` attribute of pop3.

        The resulting population, pop3, will inherit the `ga_tools`
        attribute from pop1.

        Parameters
        ----------
        other : Population
            A collection of ``MacroMolecule`` instances to be removed
            from `self`, if held by it.

        Returns
        -------
        Population
            A flat population of ``MacroMolecule`` instances which are
            not also held in `other`.

        """

        new_pop = Population(self.ga_tools)
        new_pop.add_members(mol for mol in self
                                                if mol not in other)
        return new_pop

    def __add__(self, other):
        """
        Allows use fo the ``+`` operator.

        Creates a new ``Population`` instance which holds two
        subpopulations and no direct members. The two subpopulations
        are the two ``Population`` instances on which the ``+``
        operator was applied.

        Parameters
        ----------
        other : Population

        Returns
        -------
        Population


        """

        return Population(self, other, self.ga_tools)

    def __contains__(self, item):
        """
        Allows use of the ``in`` operator.

        Parameters
        ----------
        item : MacroMolecule

        Returns
        -------
        bool

        """

        return any(item.same(mol) for mol in self.all_members())

    def __str__(self):
        output_string = (" Population " + str(id(self)) + "\n" +
                            "--------------------------\n" +
                            "\tMembers\n" + "   ---------\n")

        for mol in self.members:
            output_string += "\t"  + str(mol) + "\n"

        if len(self.members) == 0:
            output_string += "\tNone\n\n"

        output_string += (("\tSub-populations\n" +
                           "   -----------------\n\t"))

        for pop in self.populations:
            output_string += str(id(pop)) + ", "

        if len(self.populations) == 0:
            output_string += "None\n\n"

        output_string += "\n\n"

        for pop in self.populations:
            output_string += str(pop)


        return output_string

    def __repr__(self):
        return str(self)

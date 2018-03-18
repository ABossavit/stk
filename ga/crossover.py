"""
Defines crossover operations via the :class:`Crossover` class.

.. _`adding crossover functions`:

Extending stk: Adding crossover functions.
------------------------------------------

If a new crossover operation is to be added to ``stk`` it should be
added as a method in the :class:`Crossover` class defined in this
module. The only requirements are that the first two arguments are
`macro_mol1` and `macro_mol2` (excluding `self` or `cls`) and that any
offspring are returned in a :class:`.GAPopulation` instance.

The naming requirement of `macro_mol1` and `macro_mol2` exists to
help users identify which arguments are handled automatically by
``stk`` and which they need to define in the input file. The convention
is that if the crossover function takes arguments called  `macro_mol1`
and `macro_mol2` they do not have to be specified in the input file.

If the crossover function does not fit neatly into a single function
make sure that any helper functions are private, i.e. that their names
start with a leading underscore.

"""

import logging
from collections import Counter
import numpy as np
from itertools import islice

from .ga_population import GAPopulation
from .plotting import plot_counter


logger = logging.getLogger(__name__)


class Crossover:
    """
    Carries out crossover operations on the population.

    Instances of :class:`.GAPopulation` delegate crossover operations
    to instances of this class. They do this by calling

    >>> offspring_pop = pop.gen_offspring()

    where ``offspring_pop`` is a new :class:`.GAPopulation`, holding
    molecules generated by performing crossover operations on members
    of ``pop``. This class uses the :class:`.Selection` instance in
    ``pop.ga_tools.selection`` to select parents used in crossover.

    Attributes
    ----------
    funcs : :class:`list` of :class:`.FunctionData`
        This lists holds all the crossover functions which are to be
        used. One will be chosen at random when a crossover operation
        is to be performed. The likelihood that each is selected is
        given by :attr:`weights`.

    num_crossovers : :class:`int`
        The number of crossover operations performed each time
        :meth:`.GAPopulation.gen_offspring` is called.

    weights : :class:`list` of :class:`float`
        Each float corresponds to the probability of selecting the
        crossover function in :attr:`funcs` at the corresponding index.
        For example,

        .. code-block:: python

            selection = Selection(funcs=[FunctionData('one'),
                                         FunctionData('two')],
                                  num_crossovers=3,
                                  weights=[0.3, 0.7])

        means that the crossover function called "one" has a
        probability of ``0.3`` of being used, while the crossover
        function called "two" has a probability of ``0.7`` of being
        used.

        This means entries in this list must sum to 1 and the number of
        entries must be the same as in :attr:`funcs`. Defaults to
        ``None``, which means all crossover functions have an equal
        probability of selection.

    """

    def __init__(self, funcs, num_crossovers, weights=None):
        """
        Intializes a :class:`Crossover` object.

        Parameters
        ----------
        funcs : :class:`list` of :class:`.FunctionData`
            This lists holds all the crossover functions which are to
            be used. One will be chosen at random when a crossover
            operation is to be performed. The likelihood that each is
            selected is given by :attr:`weights`.

        num_crossovers : :class:`int`
            The number of crossover operations performed each time
            :meth:`.GAPopulation.gen_offspring` is called.

        weights : :class:`list` of :class:`float`, optional
            Each float corresponds to the probability of selecting the
            crossover function in :attr:`funcs` at the corresponding
            index. This means entries in this list must sum to 1 and
            the number of entries must be the same as in :attr:`funcs`.
            Defaults to ``None``, which means all crossover functions
            have an equal probability of selection.

        """

        self.funcs = funcs
        self.weights = weights
        self.num_crossovers = num_crossovers

    def __call__(self, population, counter_path=''):
        """
        Carries out crossover operations on `population`.

        This function selects members of `population` and crosses
        them until either all possible parents have been crossed or the
        required number of successful crossover operations has been
        performed.

        The offspring generated are returned together in a
        :class:`.GAPopulation` instance. Any molecules that are created
        via crossover and match a molecule present in the original
        population are removed.

        Parameters
        ----------
        population : :class:`.GAPopulation`
            The population instance who's members are to be crossed.

        counter_path : :class:`str`, optional
            The name of the ``.png`` file showing which members were
            selected for crossover. If ``''``, then no file is made.

        Returns
        -------
        :class:`.GAPopulation`
            A population with all the offspring generated held in its
            :attr:`~.Population.members` attribute. This does not
            include offspring which correspond to molecules already
            present in `population`.

        """

        offspring_pop = GAPopulation(ga_tools=population.ga_tools)
        counter = Counter()

        parent_pool = islice(population.select('crossover'),
                             self.num_crossovers)
        for i, parents in enumerate(parent_pool, 1):
            logger.info('Crossover number {}. Finish when {}.'.format(
                                           i, self.num_crossovers))
            counter.update(parents)
            # Get the crossover function.
            func_data = np.random.choice(self.funcs, p=self.weights)
            func = getattr(self, func_data.name)

            try:
                # Apply the crossover function and supply any
                # additional arguments to it.
                offspring = func(*parents, **func_data.params)

                # Print the names of offspring which have been returned
                # from the cache.
                for o in offspring:
                    if o.name:
                        logger.debug(('Offspring "{}" retrieved '
                                      'from cache.').format(o.name))

                # Add the new offspring to the offspring population.
                offspring_pop.add_members(offspring)

            except Exception as ex:
                errormsg = ('Crossover function "{}()" failed on '
                            'molecules PARENTS.').format(
                            func_data.name)

                pnames = ' and '.join('"{}"'.format(p.name) for
                                      p in parents)
                errormsg = errormsg.replace('PARENTS', pnames)
                logger.error(errormsg, exc_info=True)

        # Make sure that only original molecules are left in the
        # offspring population.
        offspring_pop -= population

        if counter_path:
            # Update counter with unselected members and plot counter.
            for member in population:
                if member not in counter.keys():
                    counter.update({member: 0})
            plot_counter(counter, counter_path)

        return offspring_pop

    """
    The following crossover operations apply to ``Cage`` instances

    """

    def bb_lk_exchange(self, macro_mol1, macro_mol2):
        """
        Exchanges the building blocks and linkers of cages.

        This operation is basically::

            bb1-lk1 + bb2-lk2 --> bb1-lk2 + bb2-lk1,

        where bb-lk represents a building block - linker combination
        of a cage.

        If the parent cages do not have the same topology, then pairs
        of offspring are created for each topology. This means that
        there may be up to ``4`` offspring.

        Parameters
        ----------
        macro_mol1 : :class:`.Cage`
            The first parent cage. Its building-block* and linker are
            combined with those of `cage2` to form new cages.

        macro_mol2 : :class:`.Cage`
            The second parent cage. Its building-block* and linker are
            combined with those of `cage1` to form new cages.

        Returns
        -------
        :class:`.GAPopulation`
            A population of all the offspring generated by crossover of
            `macro_mol1` with `macro_mol2`.

        """

        Cage = macro_mol1.__class__

        # Make a variable for each building-block* and linker of each
        # each cage. Make a set consisting of topologies of the cages
        # provided as arguments - this automatically removes copies.
        # For each topology create two offspring cages by combining the
        # building-block* of one cage with the linker of the other.
        # Place each new cage into a ``GAPopulation`` instance and return
        # that.

        _, c1_lk = max(zip(macro_mol1.bb_counter.values(),
                           macro_mol1.bb_counter.keys()))
        _, c1_bb = min(zip(macro_mol1.bb_counter.values(),
                           macro_mol1.bb_counter.keys()))

        _, c2_lk = max(zip(macro_mol2.bb_counter.values(),
                           macro_mol2.bb_counter.keys()))
        _, c2_bb = min(zip(macro_mol2.bb_counter.values(),
                           macro_mol2.bb_counter.keys()))

        offspring_pop = GAPopulation()
        # For each topology create a new pair of offspring using the
        # building block pairings determined earlier.
        topologies = (x.topology for x in (macro_mol1, macro_mol2))
        for topology in topologies:
            offspring1 = Cage((c1_lk, c2_bb), topology)
            offspring2 = Cage((c2_lk, c1_bb), topology)
            offspring_pop.add_members((offspring1, offspring2))

        return offspring_pop

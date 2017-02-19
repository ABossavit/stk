"""
Defines optimization functions.

Extending MMEA: Adding optimization functions
---------------------------------------------
New optimization functions are added by writing them into this module.
The only requirement is that the first argument is ``macro_mol``. The
requirement allows users to identify which arguments are handled
automatically by MMEA and which need to be defined in the input file.
The convention is that if the optimization function takes an argument
called ``macro_mol`` the user does not have to specify that argument in
the input file.

An optimization function should update both the file of the molecule
and the `mol` attribute.

The return values of optimization functions are discarded.

Optimizations can be complicated. If the use of helper functions is
required make sure that they are private, ie that their names begin
with a leading underscore. In the event that the optimization is so
complex that it requires its own module or file, place it in the same
folder as this file. Then import the optimization function into this
file. See ``macromodel.py`` as an example. Make sure that only the
optimization functions are imported back into this file, not any of the
helper functions or classes.

"""

import rdkit.Chem.AllChem as ac
import rdkit.Chem as chem
import multiprocessing as mp
from functools import partial, wraps

from ...convenience_tools import MolError
from .macromodel import (macromodel_opt,
                         macromodel_cage_opt, macromodel_md_opt)

def _optimize_all(func_data, population):
    """
    Run opt function on all population members in parallel.

    Parameters
    ----------
    func_data : FunctionData
        The ``FunctionData`` object which represents the chosen
        optimization function. This function should be defined within
        this module. The ``FunctionData`` object also holds any
        additional parameters the optimization function may need.

    population : Population
        The ``Population`` instance who's members must be optimized.

    Returns
    -------
    iterator of Molecule objects
        This iterator yields the optimized molecule objects.

    """

    # Using the name of the function stored in `func_data` get the
    # function object from one of the functions defined within the
    # module.
    func = globals()[func_data.name]
    # Provide the function with any additional paramters it may
    # require.
    p_func = _OptimizationFunc(partial(func, **func_data.params))

    # Apply the function to every member of the population, in
    # parallel.
    with mp.get_context('spawn').Pool() as pool:
        optimized = pool.map(p_func, population)
        # Make sure the cache is updated with the optimized versions.
        for member in optimized:
            member.update_cache()
        return optimized

def _optimize_all_serial(func_data, population):
    """
    Run opt function on all population members sequentially.

    Parameters
    ----------
    func_data : FunctionData
        The ``FunctionData`` object which represents the chosen
        optimization function. This function should be defined within
        this module. The ``FunctionData`` object also holds any
        additional parameters the optimization function may need.

    population : Population
        The ``Population`` instance who's members must be optimized.

    Returns
    -------
    iterator of Molecule objects
        This iterator yields the optimized molecule objects.


    """

    # Using the name of the function stored in `func_data` get the
    # function object from one of the functions defined within the
    # module.
    func = globals()[func_data.name]
    # Provide the function with any additional paramters it may require.
    p_func = _OptimizationFunc(partial(func, **func_data.params))

    # Apply the function to every member of the population.
    return (p_func(member) for member in population)

class _OptimizationFunc:
    """
    A decorator for optimziation functions.

    This decorator is applied to all optimization functions
    automatically in _optimize_all(). It should not be applied
    explicitly when defining the functions.

    This decorator prevents optimization functions from raising if
    they fail (necessary for multiprocessing) and prevents them from
    being run twice on the same molecule.

    """

    def __init__(self, func):
        wraps(func)(self)

    def __call__(self, macro_mol, *args,  **kwargs):
        try:
            if macro_mol.optimized:
                print('Skipping {0}'.format(macro_mol.file))
                return macro_mol

            print('\nOptimizing {0}.'.format(macro_mol.file))
            self.__wrapped__(macro_mol, *args, **kwargs)
            self.optimized = True
            return macro_mol

        except Exception as ex:
            # Prevents the error from being raised, but records it in
            # ``failures.txt``.
            macro_mol.optimized = True
            macro_mol.failed = True
            MolError(ex, macro_mol, "During optimization.")
            return macro_mol

def rdkit_optimization(macro_mol):
    """
    Optimizes the structure of the molecule using rdkit.

    Parameters
    ----------
    macro_mol : MacroMolecule
        The macromolecule who's structure should be optimized.

    Modifies
    --------
    macro_mol.mol
        The rdkit molecule held in this attribute has it's structure
        changed as a result of the optimization. This means the
        ``Conformer`` instance held by the rdkit molecule is changed.

    macro_mol.file's content
        The content of the structure file located at
        `macro_mol.file`, is changed so that it holds the structure of
        the optimized rdkit molecule.

    Returns
    -------
    None : NoneType

    """

    # Sanitize then optimize the rdkit molecule.
    chem.SanitizeMol(macro_mol.mol)
    ac.MMFFOptimizeMolecule(macro_mol.mol)

    # Update the content of the structure file.
    macro_mol.write()

def do_not_optimize(macro_mol):
    """
    Skips the optimization step.

    This is very useful when debugging so you do not waste your time
    waiting for molecules to get optimized. Use this in the input file
    in place of an optimization function when necessary.

    Parameters
    ----------
    macro_mol : MacroMolecule
        A macromolecule which will not be optimized.

    Returns
    -------
    None : NoneType

    """

    return

def raiser(macro_mol, param1, param2=2):
    """
    Doens't optimize, raises an error instead.

    This function is used to test that when optimization functions raise
    errors during multiprocessing, they are handled correctly.

    Parameters
    ---------
    param1 : object
        Dummy parameter, does nothing.

    param2 : object (default = 2)
        Dummy keyword parameter, does nothing.

    Returns
    -------
    This function does not return. It only raises.

    Raises
    ------
    Exception
        An exception is always raised.

    """

    raise Exception('Raiser optimization function used.')
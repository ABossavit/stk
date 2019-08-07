"""
Defines MacroModel optimizers.

"""

import os
import subprocess as sp
import time
import rdkit.Chem.AllChem as rdkit
import psutil
import re
from uuid import uuid4
import logging
import gzip

from ...utilities import MAEExtractor, move_generated_macromodel_files
from .optimizers import Optimizer

logger = logging.getLogger(__name__)


class MacroModelConversionError(Exception):
    ...


class MacroModelPathError(Exception):
    ...


class MacroModelForceFieldError(Exception):
    ...


class MacroModelOptimizationError(Exception):
    ...


class MacroModelLewisStructureError(Exception):
    ...


class MacroModelInputError(Exception):
    ...


class _MacroModel(Optimizer):
    """
    Base class for MacroModel optimzers.

    """

    def __init__(
        self,
        macromodel_path,
        output_dir,
        timeout,
        force_field,
        maximum_iterations,
        minimum_gradient,
        use_cache
    ):
        """
        Initializes a :class:`_MacroModel` instance.

        Parameters
        ----------
        macromodel_path : :class:`str`
            The full path of the Schrodinger suite within the user's
            machine. For example, on a Linux machine this may be
            something like ``'/opt/schrodinger2017-2'``.

        output_dir : :class:`str`, optional
            The name of the directory into which files generated during
            the optimization are written, if ``None`` then
            :func:`uuid.uuid4` is used.

        timeout : :class:`float`, optional
            The amount in seconds the optimization is allowed to run
            before being terminated. ``None`` means there is no
            timeout.

        force_field : :class:`int`, optional
            The number of the force field to be used.

        maximum_iterations : :class:`int`, optional
            The maximum number of iterations done during the
            optimization. Cannot be more than ``999999``.

        minimum_gradient : :class:`float`, optional
            The gradient at which optimization is stopped.
            Cannot be less than ``0.0001``.

        use_cache : :class:`bool`, optional
            If ``True`` :meth:`optimize` will not run twice on the same
            molecule.

        """

        self._macromodel_path = macromodel_path
        self._output_dir = output_dir
        self._timeout = timeout
        self._force_field = force_field
        self._maximum_iterations = maximum_iterations
        self._minimum_gradient = minimum_gradient
        super().__init__(use_cache=use_cache)

    def _run_bmin(self, mol):
        """
        Runs an optimization using bmin.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule being optimized.

        Returns
        -------
        None : :class:`NoneType`

        Raises
        ------
        :class:`MacroModelOptimizationError`
            If the optimization failed for some unspecified.

        :class:`MacroModelForceFieldError`
            If the force field could not be used with the molecule.

        :class:`MacroModelLewisStructureError`
            If Lewis structure of the molecule had issues.

        :class:`MacroModelPathError`
            If an invalid MacroModel path is being used.

        """

        logger.info(f'Running bmin on "{mol}".')

        # To run MacroModel a command is issued to the console via
        # ``subprocess.Popen``. The command is the full path of the
        # ``bmin`` program. ``bmin`` is located in the Schrodinger
        # installation folder.
        file_root, ext = os.path.splitext(mol._file)
        log_file = f'{file_root}.log'
        opt_app = os.path.join(self._macromodel_path, 'bmin')
        # The first member of the list is the command, the following
        # ones are any additional arguments.

        opt_cmd = [opt_app, file_root, '-WAIT', '-LOCAL']

        incomplete = True
        while incomplete:
            process = psutil.Popen(
                opt_cmd,
                stdout=sp.PIPE,
                stderr=sp.STDOUT,
                universal_newlines=True
            )
            try:
                output, _ = process.communicate(timeout=self._timeout)

            except sp.TimeoutExpired:
                logger.warning(
                    'Minimization took too long and was terminated '
                    f'by force on "{mol}".'
                )
                self._kill_bmin(mol)
                output = ''

            logger.debug(
                f'Output of bmin on "{mol}" was: {output}.'
            )

            with open(log_file, 'r') as log:
                log_content = log.read()

            # Check the log for error reports.
            error1 = 'termination due to error condition           21-'
            if error1 in log_content:
                raise MacroModelOptimizationError(
                    'bmin crashed. See log file.'
                )

            error2 = 'FATAL do_nosort_typing: NO MATCH found for atom '
            if error2 in log_content:
                raise MacroModelForceFieldError(
                    'The log implies the force field failed.'
                )

            error3 = (
                'FATAL gen_lewis_structure(): '
                'could not find best Lewis structure'
            )
            error4 = (
                'skipping input structure  '
                'due to forcefield interaction errors'
            )
            if error3 in log_content and error4 in log_content:
                raise MacroModelLewisStructureError(
                    'bmin failed due to poor Lewis structure.'
                )

            if 'MDYN error encountered' in log_content:
                raise MacroModelOptimizationError(
                    'MD error during optimization.'
                )

            # If optimization fails because a wrong Schrodinger path
            # was given, raise.
            if 'The system cannot find the path specified' in output:
                raise MacroModelPathError(
                    'Invalid Schrodinger path given to bmin.'
                )

            # If optimization fails because the license is not found,
            # rerun the function.
            if self._license_found(output, mol):
                incomplete = False

        # Make sure the .maegz file created by the optimization is
        # present.
        maegz = file_root + '-out.maegz'
        self._wait_for_file(maegz)
        if not os.path.exists(log_file) or not os.path.exists(maegz):
            raise MacroModelOptimizationError(
                'The .log and/or .maegz files were not created.'
            )

    def _kill_bmin(self, mol):
        """
        Kills bmin.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule being optimized.

        Returns
        -------
        None : :class:`NoneType`

        """

        name, ext = os.path.splitext(mol._file)
        name = re.split(r'\\|/', name)[-1]
        app = os.path.join(self._macromodel_path, 'jobcontrol')
        cmd = [app, '-stop', name]

        incomplete = True
        while incomplete:
            out = sp.run(
                cmd,
                stdout=sp.PIPE,
                stderr=sp.STDOUT,
                universal_newlines=True
            )

            # Keep re-running the function until license is found.
            if self._license_found(out.stdout):
                incomplete = False

        # This loop causes the function to wait until the job has been
        # killed via job control. This means the output files will have
        # been written by the time the function exits. Essentially the
        # loop continues until the job is no longer found by
        # "./jobcontrol -list"
        cmd = [app, '-list']
        output = name
        start = time.time()
        while name in output:
            output = sp.run(
                cmd,
                stdout=sp.PIPE,
                stderr=sp.STDOUT,
                universal_newlines=True
            ).stdout
            if time.time() - start > 600:
                break

    def _license_found(self, output, mol=None):
        """
        Checks to see if minimization failed due to a missing license.

        The user can be notified of this in one of two ways. Sometimes
        the output of the submission contains the message informing
        that the license was not found and in other cases it will be
        the log file. This function checks both of these sources for
        this message.

        Parameters
        ----------
        output : :class:`str`
            The output from submitting the minimization of the
            structure to bmin.

        mol : :class:`.Molecule`, optional
            The molecule being optimized. If the ``.log`` file is not
            to be checked, the default ``None`` should be used.

        Returns
        -------
        :class:`bool`
            ``True`` if the license was found. ``False`` if the
            minimization did not occur due to a missing license.

        """

        if 'Could not check out a license for mmlibs' in output:
            return False
        if mol is None:
            return True

        # To check if the log file mentions a missing license file open
        # the log file and scan for the apporpriate string.

        # Check if the file exists first. If not, this is often means
        # the calculation must be redone so return False anyway.
        log_file_path = mol._file.replace('mol', 'log')
        with open(log_file_path, 'r') as log_file:
            log_file = log_file.read()

        if 'Could not check out a license for mmlibs' in log_file:
            return False

        return True

    @staticmethod
    def _com_line(arg1, arg2, arg3, arg4, arg5, arg6, arg7, arg8, arg9):
        return (
            f' {arg1:<5}{arg2:>7}{arg3:>7}'
            f'{arg4:>7}{arg5:>7}{arg6:>11.4f}'
            f'{arg7:>11.4f}{arg8:>11.4f}{arg9:>11.4f}'
        )

    def _structconvert(self, iname, oname):
        """
        Uses structconvert to change file type.

        Parameters
        ----------
        iname : :class:`str`
            The name of the input file.

        oname : :class:`str`
            The name of the output file.

        Returns
        -------
        None : :class:`NoneType`

        """

        convrt_app = os.path.join(
            self._macromodel_path, 'utilities', 'structconvert'
        )
        convrt_cmd = [convrt_app, iname, oname]

        incomplete = True
        while incomplete:

            # Execute the file conversion.
            try:
                convrt_return = sp.run(
                    convrt_cmd,
                    stdout=sp.PIPE,
                    stderr=sp.STDOUT,
                    universal_newlines=True
                )

            # If conversion fails because a wrong Schrodinger path was
            # given, raise.
            except FileNotFoundError:
                raise MacroModelPathError(
                    'Wrong Schrodinger path supplied to structconvert.'
                )

            if 'File does not exist' in convrt_return.stdout:
                raise MacroModelConversionError(
                    f'structconvert input file, {iname}, missing. '
                    f'Console output was {convrt_return.stdout}'
                )

            # Keep re-running the function until license is found.
            if self._license_found(convrt_return.stdout):
                incomplete = False

        # If force field failed, raise.
        if 'number 1' in convrt_return.stdout:
            raise MacroModelForceFieldError(convrt_return.stdout)

        self._wait_for_file(oname)
        if not os.path.exists(oname):
            raise MacroModelConversionError(
                f'Conversion output file {oname} was not found.'
                f' Console output was {convrt_return.stdout}.'
            )

        return convrt_return

    def _create_mae(self, mol):
        """
        Creates the ``.mae`` file holding the molecule to be optimized.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule which is to be optimized. Its molecular
            structure file is converted to a ``.mae`` file. The
            original file is also kept.

        Returns
        -------
        :class:`str`
            The full path of the newly created ``.mae`` file.

        """

        _, ext = os.path.splitext(mol._file)

        logger.debug(f'Converting {ext} of "{mol}" to .mae.')

        # Create the name of the new ``.mae`` file. It is the same as
        # the original structure file, including the same path. Only
        # the extensions are different.
        mae_file = mol._file.replace(ext, '.mae')
        self._structconvert(mol._file, mae_file)
        return mae_file

    def _wait_for_file(self, filename, timeout=10):
        """
        Stalls until a given file exists or `timeout` expires.

        Parameters
        ----------
        filename : :class:`str`
            The full path of the file which should be waited for.

        timeout : :class:`int` or :class:`float`, optional
            The number of seconds before the function stops waiting and
            returns.

        Returns
        --------
        None : :class:`NoneType`

        """

        t_start = time.time()
        tick = 0
        while True:
            time_taken = time.time() - t_start
            if divmod(time_taken, 5)[0] == tick + 1:
                logger.warning(f'Waiting for "{filename}".')
                tick += 1

            if os.path.exists(filename) or time_taken > timeout:
                break

    def _convert_maegz_to_mae(self, mol):
        """
        Converts a ``.maegz`` file to a ``.mae`` file.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule being optimized. The ``.maegz`` file holding
            its optimized structure is converted to a ``.mae`` file.
            Both versions are kept.

        Returns
        -------
        None : :class:`NoneType`

        """

        logger.debug(f'Converting .maegz of "{mol}" to .mae.')
        name, ext = os.path.splitext(mol._file)
        # ``out`` is the full path of the optimized ``.mae`` file.
        maegz = name + '-out.maegz'
        # Replace extensions to get the names of the various files.
        mae = name + '.mae'

        gz_file = gzip.open(maegz)
        with open(mae, 'wb') as f:
            f.write(gz_file.read())
        gz_file.close()

    def _fix_params(self, mol, com):
        """
        Fix bond distances and angles in ``.com`` file.

        For each bond distance, bond angle and torisional angle that
        does not involve a bond created by
        :meth:`~.Topology.construct`, a "FX" command is added to the
        body of the ``.com`` file.

        These lines replace the filler line in the main string.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule which is to be optimized.

        com : :class:`str`
            The body of the ``.com`` file which is to have fix commands
            added.

        Returns
        -------
        :class:`str`
            A string holding the body of the ``.com`` file with
            instructions to fix the various bond distances and angles
            as described in the docstring.

        """

        fix_block = ''
        # Add lines that fix the bond distance.
        fix_block = self._fix_distances(mol, fix_block)
        # Add lines that fix the bond angles.
        fix_block = self._fix_bond_angles(mol, fix_block)
        # Add lines that fix the torsional angles.
        fix_block = self._fix_torsional_angles(mol, fix_block)

        return com.replace(
            '!!!BLOCK_OF_FIXED_PARAMETERS_COMES_HERE!!!\n',
            fix_block
        )


class MacroModelForceField(_MacroModel):
    """
    Uses MacroModel force fields to optimize molecules.

    """

    def __init__(
        self,
        macromodel_path,
        output_dir=None,
        restricted=False,
        timeout=None,
        force_field=16,
        maximum_iterations=2500,
        minimum_gradient=0.05,
        use_cache=False
    ):
        """
        Initializes a :class:`MacroModelForceField` object.

        Parameters
        ----------
        macromodel_path : :class:`str`
            The full path of the Schrodinger suite within the user's
            machine. For example, on a Linux machine this may be
            something like ``'/opt/schrodinger2017-2'``.

        output_dir : :class:`str`, optional
            The name of the directory into which files generated during
            the optimization are written, if ``None`` then
            :func:`uuid.uuid4` is used.

        restricted : :class:`bool`, optional
            If ``True`` then an optimization is performed only on the
            bonds added by :meth:`~.Topology.construct`. If ``False``
            then all bonds are optimized.

        timeout : :class:`float`, optional
            The amount in seconds the optimization is allowed to run
            before being terminated. ``None`` means there is no
            timeout.

        force_field : :class:`int`, optional
            The number of the force field to be used.

        maximum_iterations : :class:`int`, optional
            The maximum number of iterations done during the
            optimization. Cannot be more than ``999999``.

        minimum_gradient : :class:`float`, optional
            The gradient at which optimization is stopped.
            Cannot be less than ``0.0001``.

        use_cache : :class:`bool`, optional
            If ``True`` :meth:`optimize` will not run twice on the same
            molecule.

        """
        self._check_params(
            minimum_gradient=minimum_gradient,
            maximum_iterations=maximum_iterations
        )
        self._restricted = restricted
        super().__init__(
            macromodel_path=macromodel_path,
            output_dir=output_dir,
            force_field=force_field,
            maximum_iterations=maximum_iterations,
            minimum_gradient=minimum_gradient,
            timeout=timeout,
            use_cache=use_cache
        )

    @staticmethod
    def _check_params(minimum_gradient, maximum_iterations):
        """
        Check if the optimization parameters are valid for MacroModel.

        Parameters
        ----------
        minimum_gradient : :class:`float`
            The gradient at which optimization is stopped.
            Cannot be less than ``0.0001``.

        maximum_iterations : :class:`int`
            The maximum number of iterations done during the
            optimization. Cannot be more than ``999999``.

        Returns
        -------
        None : :class:`NoneType`

        Raises
        ------
        :class:`.MacroModelInputError`
            If the parameters cannot be converted into a valid ``.com``
            file entry.

        """

        if minimum_gradient < 0.0001:
            raise MacroModelInputError(
                'Convergence gradient (< 0.0001) is too small.'
            )

        if maximum_iterations > 999999:
            raise MacroModelInputError(
                'Number of iterations (> 999999) is too high.'
            )

    def _generate_com(self, mol):
        """
        Create a ``.com`` file for a MacroModel optimization.

        The created ``.com`` file fixes all bond parameters which were
        not added by :meth:`~.Topology.construct`. This means all bond
        distances, bond angles and torsional angles are fixed, except
        for cases where it involves a bond added by
        :meth:`.Topology.construct`.

        This fixing is implemented by creating a ``.com`` file with
        various "FX" commands written within its body.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule which is to be optimized.

        Returns
        -------
        None : :class:`NoneType`

        """

        logger.debug(f'Creating .com file for "{mol}".')

        # This is the body of the ``.com`` file. The line that begins
        # and ends with exclamation lines is replaced with the various
        # commands that fix bond distances and angles.
        line1 = ('FFLD', self._force_field, 1, 0, 0, 1, 0, 0, 0)
        line2 = ('BGIN', 0, 0, 0, 0, 0, 0, 0, 0)
        line3 = ('READ', 0, 0, 0, 0, 0, 0, 0, 0)
        line4 = ('CONV', 2, 0, 0, 0, self._minimum_gradient, 0, 0, 0)
        line5 = ('MINI', 1, 0, self._maximum_iterations, 0, 0, 0, 0, 0)
        line6 = ('END', 0, 1, 0, 0, 0, 0, 0, 0)

        com_block = "\n".join([
            self._com_line(*line1),
            self._com_line(*line2),
            self._com_line(*line3),
            '!!!BLOCK_OF_FIXED_PARAMETERS_COMES_HERE!!!',
            self._com_line(*line4),
            self._com_line(*line5),
            self._com_line(*line6)
        ])

        # Create a path for the ``.com`` file. It is the same as that
        # of the structure file but with a ``.com`` extension.
        name, ext = os.path.splitext(mol._file)

        # If `restricted` is ``False`` do not add a fix block.
        if not self._restricted:
            com_block = com_block.replace(
                "!!!BLOCK_OF_FIXED_PARAMETERS_COMES_HERE!!!\n",
                ''
            )
        else:
            # This function adds all the lines which fix bond distances
            # and angles into com_block.
            com_block = self._fix_params(mol, com_block)

        # Writes the .com file.
        with open(f'{name}.com', 'w') as com:
            # The first line holds the .mae file containing the
            # molecule to be optimized.
            com.write(f'{name}.mae\n')
            # The second line holds the name of the output file of the
            # optimization.
            com.write(f'{name}-out.maegz\n')
            # Next is the body of the .com file.
            com.write(com_block)

    def optimize(self, mol):
        """
        Optimizes a molecule.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        Returns
        -------
        None : :class:`NoneType`

        """

        basename = str(uuid4().int)
        if self._output_dir is None:
            output_dir = basename
        else:
            output_dir = self._output_dir

        mol._file = f'{basename}.mol'

        # First write a .mol file of the molecule.
        mol.write(mol._file)
        # MacroModel requires a ``.mae`` file as input.
        self._create_mae(mol)
        # generate the ``.com`` file for the MacroModel run.
        self._generate_com(mol)
        # Run the optimization.
        self._run_bmin(mol)
        # Get the ``.maegz`` optimization output to a ``.mae``.
        self._convert_maegz_to_mae(mol)
        mol.update_from_file(f'{basename}.mae')

        move_generated_macromodel_files(basename, output_dir)

    def _fix_distances(self, mol, fix_block):
        """
        Adds lines fixing bond distances to ``.com`` body.

        Only bond distances which do not involve bonds created during
        construction are fixed.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        fix_block : :class:`str`
            A string holding fix commands in the ``.com`` file.

        Returns
        -------
        :class:`str`
            A string holding fix commands in the ``.com`` file.

        """

        bonder_ids = set(
            id_
            for fg in mol.func_groups
            for id_ in fg.get_bonder_ids()
        )

        # Go through all the bonds in the rdkit molecule. If the bond
        # is not between bonder atoms add a fix line to the
        # ``fix_block``. If the bond does invovle two bonder atoms go
        # to the next bond. This is because a bond between 2 bonder
        # atoms was added during construction and should therefore not
        # be fixed.
        for bond in mol.bonds:

            if (bond.atom1.id in bonder_ids
               and bond.atom2.id in bonder_ids):
                continue

            # Make sure that the indices are increased by 1 in the .mae
            # file.
            atom1_id = bond.atom1.id + 1
            atom2_id = bond.atom2.id + 1
            args = ('FXDI', atom1_id, atom2_id, 0, 0, 99999, 0, 0, 0)
            fix_block += self._com_line(*args)
            fix_block += '\n'

        return fix_block

    def _fix_bond_angles(self, mol, fix_block):
        """
        Adds lines fixing bond angles to the ``.com`` body.

        All bond angles of the molecule are fixed.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        fix_block : :class:`str`
            A string holding fix commands in the ``.com`` file.

        Returns
        -------
        :class:`str`
            A string holding fix commands in the ``.com`` file.

        """

        paths = rdkit.FindAllPathsOfLengthN(
            mol=mol.to_rdkit_mol(),
            length=3,
            useBonds=False,
            useHs=True
        )
        for atom_ids in paths:
            atom_ids = [i+1 for i in atom_ids]
            args = ('FXBA', *atom_ids, 99999, 0, 0, 0, 0)
            fix_block += self._com_line(*args)
            fix_block += '\n'

        return fix_block

    def _fix_torsional_angles(self, mol, fix_block):
        """
        Adds lines fixing torsional bond angles to the ``.com`` body.

        All torsional angles of the molecule are fixed.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        fix_block : :class:`str`
            A string holding fix commands in the ``.com`` file.

        Returns
        -------
        :class:`str`
            A string holding fix commands in the ``.com`` file.

        """

        paths = rdkit.FindAllPathsOfLengthN(
            mol=mol.to_rdkit_mol(),
            length=4,
            useBonds=False,
            useHs=True
        )
        for atom_ids in paths:
            atom_ids = [i+1 for i in atom_ids]
            args = ('FXTA', *atom_ids, 99999, 361, 0, 0)
            fix_block += self._com_line(*args)
            fix_block += '\n'

        return fix_block


class MacroModelMD(_MacroModel):
    """
    Runs a molecular dynamics conformer search using MacroModel.

    """

    def __init__(
        self,
        macromodel_path,
        output_dir=None,
        timeout=None,
        force_field=16,
        temperature=300,
        conformers=50,
        time_step=1,
        eq_time=10,
        simulation_time=200,
        maximum_iterations=2500,
        minimum_gradient=0.05,
        restricted_bonds=None,
        restricted_bond_angles=None,
        restricted_torsional_angles=None,
        use_cache=False
    ):
        """
        Runs a MD conformer search on `mol`.

        Parameters
        ----------
        macromodel_path : :class:`str`
            The full path of the Schrodinger suite within the user's
            machine. For example, on a Linux machine this may be
            something like ``'/opt/schrodinger2017-2'``.

        output_dir : :class:`str`, optional
            The name of the directory into which files generated during
            the optimization are written, if ``None`` then
            :func:`uuid.uuid4` is used.

        timeout : :class:`float`, optional
            The amount in seconds the MD is allowed to run before
            being terminated. ``None`` means there is no timeout.

        force_field : :class:`int`, optional
            The number of the force field to be used.

        temperature : :class:`float`, optional
            The temperature in Kelvin at which the MD is run.
            Cannot be more than ``99999.99``.

        conformers' : :class:`int`, optional
            The number of conformers sampled and optimized from the MD.
            Cannot be more than ``9999``.

        simulation_time : :class:`float`, optional
            The simulation time in ``ps`` of the MD.
            Cannot be more than ``999999.99``.

        time_step : :class:`float`, optional
            The time step in ``fs`` for the MD.
            Cannot be more than ``99999.99``.

        eq_time : :class:`float`, optional
            The equilibriation time in ``ps`` before the MD is run.
            Cannot be more than ``999999.99``.

        maximum_iterations : :class:`int`, optional
            The maximum number of iterations done during the
            optimization. Cannot be more than ``999999``.

        minimum_gradient : :class:`float`, optional
            The gradient at which optimization is stopped.
            Cannot be less than ``0.0001``.

        restricted_bonds : :class:`set`, optional
            A :class:`set` of the form

            .. code-block:: python

                restricted_bonds = {
                    frozenset((0, 10)),
                    frozenset((3, 14)),
                    frozenset((5, 6))
                }

            Where each :class:`frozenset` defines which bonds should
            have a fixed length via the atom ids of atoms in the bond.

        restricted_bond_angles : :class:`set`, optional
            A :class:`set` of the form

            .. code-block:: python

                restricted_bonds = {
                    frozenset((0, 10, 12)),
                    frozenset((3, 14, 7)),
                    frozenset((5, 8, 2))
                }

            Where each :class:`frozenset` defines which bond angles
            should have a fixed size via the atom ids of atoms in the
            bond angle.

        restricted_torsional_angles : :class:`set`, optional
            A :class:`set` of the form

            .. code-block:: python

                restricted_bonds = {
                    frozenset((0, 10, 12, 3)),
                    frozenset((3, 14, 7, 4)),
                    frozenset((5, 8, 2, 9))
                }

            Where each :class:`frozenset` defines which torsional
            angles should have a fixed size via the atom ids of atoms
            in the torsional angle.

        use_cache : :class:`bool`, optional
            If ``True`` :meth:`optimize` will not run twice on the same
            molecule.

        """

        if restricted_bonds is None:
            restricted_bonds = set()
        if restricted_bond_angles is None:
            restricted_bond_angles = set()
        if restricted_torsional_angles is None:
            restricted_torsional_angles = set()

        self._check_params(
            temperature=temperature,
            conformers=conformers,
            simulation_time=simulation_time,
            time_step=time_step,
            eq_time=eq_time,
            minimum_gradient=minimum_gradient,
            maximum_iterations=maximum_iterations
        )

        self._temperature = temperature
        self._conformers = conformers
        self._time_step = time_step
        self._eq_time = eq_time
        self._simulation_time = simulation_time
        self._restricted_bonds = restricted_bonds
        self._restricted_bond_angles = restricted_bond_angles
        self._restricted_torsional_angles = restricted_torsional_angles

        # Negative simulation time is interpreted as times 100 ps.
        if simulation_time > 99999.99:
            self._sim_time = -simulation_time/100
        else:
            self._sim_time = simulation_time

        # Negative equilibration time is interpreted as times 100 ps.
        if eq_time > 99999.99:
            self._eq_time = -eq_time/100
        else:
            self._eq_time = eq_time

        super().__init__(
            macromodel_path=macromodel_path,
            output_dir=output_dir,
            timeout=timeout,
            force_field=force_field,
            maximum_iterations=maximum_iterations,
            minimum_gradient=minimum_gradient,
            use_cache=use_cache
        )

    @staticmethod
    def _check_params(
        temperature,
        conformers,
        simulation_time,
        time_step,
        eq_time,
        minimum_gradient,
        maximum_iterations
    ):
        """
        Check if the optimization parameters are valid for MacroModel.

        Parameters
        ----------
        temperature : :class:`float`
            The temperature in Kelvin at which the MD is run.
            Cannot be more than ``99999.99``.

        conformers' : :class:`int`
            The number of conformers sampled and optimized from the MD.
            Cannot be more than ``9999``.

        simulation_time : :class:`float`
            The simulation time in ``ps`` of the MD.
            Cannot be more than ``999999.99``.

        time_step : :class:`float`
            The time step in ``fs`` for the MD.
            Cannot be more than ``99999.99``.

        eq_time : :class:`float`
            The equilibriation time in ``ps`` before the MD is run.
            Cannot be more than ``999999.99``.

        minimum_gradient : :class:`float`
            The gradient at which optimization is stopped.
            Cannot be less than ``0.0001``.

        maximum_iterations : :class:`int`
            The maximum number of iterations done during the
            optimization. Cannot be more than ``999999``.

        Returns
        -------
        None : :class:`NoneType`

        Raises
        ------
        :class:`.MacroModelInputError`
            If the parameters cannot be converted into a valid ``.com``
            file entry.

        """

        if temperature > 99999.99:
            raise MacroModelInputError(
                'Supplied temperature (> 99999 K) is too high.'
            )

        if conformers > 9999:
            raise MacroModelInputError(
                'Supplied number of conformers (> 9999) is too high.'
            )

        if simulation_time > 999999.99:
            raise MacroModelInputError(
                'Supplied simulation time (> 999999 ps) is too long.'
            )

        if time_step > 99999.99:
            raise MacroModelInputError(
                'Supplied time step (> 99999 fs) is too high.'
            )

        if eq_time > 999999.99:
            raise MacroModelInputError(
                'Supplied eq time (> 999999 ps) is too long.'
            )

        if minimum_gradient < 0.0001:
            raise MacroModelInputError(
                'Convergence gradient (< 0.0001) is too small.'
            )

        if maximum_iterations > 999999:
            raise MacroModelInputError(
                'Number of iterations (> 999999) is too high.'
            )

    def _generate_com(self, mol):
        """
        Create a ``.com`` file for a MacroModel optimization.

        The created ``.com`` file fixes all bond parameters which were
        not added by :meth:`~.Topology.construct`. This means all bond
        distances, bond angles and torsional angles are fixed, except
        for cases where it involves a bond added by
        :meth:`~.Topology.construct`.

        This fixing is implemented by creating a ``.com`` file with
        various "FX" commands written within its body.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule which is to be optimized.

        Returns
        -------
        None : :class:`NoneType`

        """

        logger.debug(f'Creating .com file for "{mol}".')

        # Define some short aliases to keep the following lines neat.
        temp = self._temperature
        sim_time = self._sim_time
        tstep = self._time_step
        eq_time = self._eq_time

        line1 = ('FFLD', self._force_field, 1, 0, 0, 1, 0, 0, 0)
        line2 = ('READ', 0, 0, 0, 0, 0, 0, 0, 0)
        line3 = ('MDIT', 0, 0, 0, 0, temp, 0, 0, 0)
        line4 = ('MDYN', 0, 0, 0, 0, tstep, eq_time, temp, 0)
        line5 = ('MDSA', self._conformers, 0, 0, 0, 0, 0, 1, 0)
        line6 = ('MDYN', 1, 0, 0, 0, tstep, sim_time, temp, 0)
        line7 = ('WRIT', 0, 0, 0, 0, 0, 0, 0, 0)
        line8 = ('RWND', 0, 1, 0, 0, 0, 0, 0, 0)
        line9 = ('BGIN', 0, 0, 0, 0, 0, 0, 0, 0)
        line10 = ('READ', -2, 0, 0, 0, 0, 0, 0, 0)
        line11 = ('CONV', 2, 0, 0, 0, self._minimum_gradient, 0, 0, 0)
        line12 = ('MINI', 1, 0, self._maximum_iterations, 0, 0, 0, 0, 0)
        line13 = ('END', 0, 1, 0, 0, 0, 0, 0, 0)

        com_block = "\n".join([
            self._com_line(*line1),
            self._com_line(*line2),
            '!!!BLOCK_OF_FIXED_PARAMETERS_COMES_HERE!!!',
            self._com_line(*line3),
            self._com_line(*line4),
            self._com_line(*line5),
            self._com_line(*line6),
            self._com_line(*line7),
            self._com_line(*line8),
            self._com_line(*line9),
            self._com_line(*line10),
            '!!!BLOCK_OF_FIXED_PARAMETERS_COMES_HERE!!!',
            self._com_line(*line11),
            self._com_line(*line12),
            self._com_line(*line13),
        ])

        com_block = self._fix_params(mol, com_block)

        name, ext = os.path.splitext(mol._file)

        # Generate the com file containing the info for the run
        with open(f'{name}.com', 'w') as com:
            # name of the macromodel file
            com.write(f'{name}.mae\n')
            # name of the output file
            com.write(f'{name}-out.maegz\n')
            # details of the macromodel run
            com.write(com_block)

    def optimize(self, mol):
        """
        Optimizes a molecule.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        Returns
        -------
        None : :class:`NoneType`

        """

        basename = str(uuid4().int)
        if self._output_dir is None:
            output_dir = basename
        else:
            output_dir = self._output_dir

        mol._file = f'{basename}.mol'

        # First write a .mol file of the molecule.
        mol.write(mol._file)
        # MacroModel requires a ``.mae`` file as input.
        self._create_mae(mol)
        # Generate the ``.com`` file for the MacroModel MD run.
        self._generate_com(mol)
        # Run the optimization.
        self._run_bmin(mol)
        # Extract the lowest energy conformer into its own .mae file.
        conformer_mae = MAEExtractor(mol._file).path
        mol.update_from_file(conformer_mae)

        move_generated_macromodel_files(basename, output_dir)

    def _fix_distances(self, mol, fix_block):
        """
        Adds lines fixing bond distances to ``.com`` body.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        fix_block : :class:`str`
            A string holding fix commands in the ``.com`` file.

        Returns
        -------
        :class:`str`
            A string holding fix commands in the ``.com`` file.

        """

        # Go through all the bonds in the rdkit molecule. If the bond
        # is not between bonder atoms add a fix line to the
        # ``fix_block``. If the bond does invovle two bonder atoms go
        # to the next bond. This is because a bond between 2 bonder
        # atoms was added during construction and should therefore not
        # be fixed.
        for bond in mol.bonds:
            bond_key = frozenset((bond.atom1.id, bond.atom2.id))
            if (bond_key not in self._restricted_bonds):
                continue

            # Make sure that the indices are increased by 1 in the .mae
            # file.
            atom1_id = bond.atom1.id + 1
            atom2_id = bond.atom2.id + 1
            args = ('FXDI', atom1_id, atom2_id, 0, 0, 99999, 0, 0, 0)
            fix_block += self._com_line(*args)
            fix_block += '\n'

        return fix_block

    def _fix_bond_angles(self, mol, fix_block):
        """
        Adds lines fixing bond angles to the ``.com`` body.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        fix_block : :class:`str`
            A string holding fix commands in the ``.com`` file.

        Returns
        -------
        :class:`str`
            A string holding fix commands in the ``.com`` file.

        """

        paths = rdkit.FindAllPathsOfLengthN(
            mol=mol.to_rdkit_mol(),
            length=3,
            useBonds=False,
            useHs=True
        )
        for atom_ids in paths:
            if frozenset(atom_ids) in self._restricted_bond_angles:
                atom_ids = [i+1 for i in atom_ids]
                args = ('FXBA', *atom_ids, 99999, 0, 0, 0, 0)
                fix_block += self._com_line(*args)
                fix_block += '\n'

        return fix_block

    def _fix_torsional_angles(self, mol, fix_block):
        """
        Adds lines fixing torsional bond angles to the ``.com`` body.

        Parameters
        ----------
        mol : :class:`.Molecule`
            The molecule to be optimized.

        fix_block : :class:`str`
            A string holding fix commands in the ``.com`` file.

        Returns
        -------
        :class:`str`
            A string holding fix commands in the ``.com`` file.

        """

        paths = rdkit.FindAllPathsOfLengthN(
            mol=mol.to_rdkit_mol(),
            length=4,
            useBonds=False,
            useHs=True
        )
        # Apply the fix.
        for atom_ids in paths:
            if frozenset(atom_ids) in self._restricted_torsional_angles:
                atom_ids = [i+1 for i in atom_ids]
                args = ('FXTA', *atom_ids, 99999, 361, 0, 0)
                fix_block += self._com_line(*args)
                fix_block += '\n'

        return fix_block

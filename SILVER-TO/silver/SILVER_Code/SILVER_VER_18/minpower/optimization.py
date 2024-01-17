"""
An optimization command library for Minpower.
Basically a wrapper around Coopr's `pyomo.ConcreteModel` class.
"""
from __future__ import print_function
from __future__ import absolute_import
from builtins import str
from builtins import object
import logging
import time
import weakref
import numpy as np

from .schedule import is_init
from .commonscripts import quiet, not_quiet, update_attributes, joindir
from pyomo import environ as pyomo
from pyomo.opt.base import solvers as cooprsolver
from .config import user_config
from past.utils import old_div
import pandas as pd
from pyomo.opt import SolverStatus, TerminationCondition
from .silver_variables import PATH
from SILVER_VER_18.config_variables import *
from pyomo.util.infeasible import log_infeasible_constraints
import SILVER_VER_18.internal_vars as internal_vars

# make pyomo recognize that True == 1
pyomo.base.numvalue = pyomo.base.numvalue.NumericConstant(1.0)

variable_kinds = dict(
    Continuous=pyomo.Reals,
    Binary=pyomo.Boolean,
    Boolean=pyomo.Boolean)


def full_filename(filename):
    return joindir(user_config.directory, filename)


class OptimizationObject(object):
    '''
    A base class for an optimization object.
    This also serves as a template for
    how :class:`~OptimizationObject`s are structured.
    '''

    def __init__(self, *args, **kwargs):
        '''
        Individual class defined.
        Initialize the object. Often this just means assigning
        all of the keyword arguments to self.
        The :meth:`~OptimizationObject.init_optimization` method
        should be called in __init__.
        '''
        update_attributes(self, locals())  # load in inputs
        self.init_optimization()

    def init_optimization(self):
        '''
        Initialize optimization components: add a container for children.
        If the index is not defined, make it a hash of the object
        to ensure the index is unique.
        '''
        self.children = {}
        if getattr(self, 'index', None) is None:
            self.index = hash(self)
        if getattr(self, 'name', None) == '':
            self.name = self.index + 1  # 1 and up naming

    def create_variables(self, times, *args, **kwargs):
        '''
        Individual class defined.
        Create the variables in this method by using calls to
        :meth:`~optimization.OptimiationObject.add_variable`.
        Variables will be accessible by using
        :meth:`~optimization.OptimiationObject.get_variable` (or by adding
        a shortcut methods, like :meth:`~powersystems.Generator.power`).
        '''
        return  # self.all_variables(times)

    def create_objective(self, times):
        '''
        Individual class defined.
        Return the contribution to the objective (cost) expression.
        :returns: an expression, the default is 0
        '''
        return 0

    def create_constraints(self, times, *args, **kwargs):
        '''
        Individual class defined.
        Create the constraints in this method by using calls to
        :meth:`~optimization.OptimiationObject.add_constraint`.
        Constraints will be accessible by using
        :meth:`~optimization.OptimiationObject.get_constraint` (or by adding
        a shortcut methods, like :meth:`~powersystems.Bus.price`.
        '''

        print("optimization py:", self.all_constraints(times))
        return

    def add_variable(self, name, time=None,
                     fixed_value=None,
                     index=None,
                     **kwargs):
        '''
        Create a new variable and add it to the object's variables and the model's variables.
        :param name: name of optimization variable.
        :param kind: type of variable, specified by string. {Continuous or Binary/Boolean}
        :param low: low limit of variable
        :param high: high limit of variable
        :param fixed_value: a fixed value for a variable (making it a parameter)
        :param time: a single time for a variable
        :param index: a :class:`pyomo.Set` over which a variable is created
        '''
        def map_args(kind='Continuous', low=None, high=None):
            return dict(bounds=(low, high), domain=variable_kinds[kind])
        orig_name = name
        if index is None:
            name = self._t_id(name, time)
            if fixed_value is None:
                var = pyomo.Var(name=name, **map_args(**kwargs))
                self._parent_problem().add_component_to_problem(var)
            else:
                var = pyomo.Param(name=name, default=fixed_value)
                # add var
                self._parent_problem().add_component_to_problem(var)
                # and set value
                var = self.get_variable(orig_name, time)
                var[None] = fixed_value
        else:
            name = self._id(name)

            if fixed_value is None:
                var = pyomo.Var(index, name=name, **map_args(**kwargs))
                self._parent_problem().add_component_to_problem(var)
            else:
                var = pyomo.Param(index, name=name, default=fixed_value, mutable=True)
                self._parent_problem().add_component_to_problem(var)
                var = self._parent_problem().get_component(name)
                for i in index:
                    var[i] = fixed_value

    def add_parameter(self, name, index=None, values=None, mutable=True, default=None, **kwargs):
        name = self._id(name)
        self._parent_problem().add_component_to_problem(
            pyomo.Param(index, name=name, mutable=mutable, default=default, **kwargs))
        if values is not None:
            if pd.Series(values).count() != len(values):
                print("error:", name, values)
                raise ValueError('a parameter value cannot be NaN')
            var = self._parent_problem().get_component(name)

            for i in index:
                var[i] = values[i]

    def add_constraint(self, name, time, expression):
        '''Create a new constraint and add it to the object's constraints and the model's constraints.'''
        cname = self._t_id(name, time)
        self._parent_problem().add_component_to_problem(pyomo.Constraint(name=cname, expr=expression))

    def add_constraint_set(self, name, index, expression):
        cname = self._id(name)
        self._parent_problem().add_component_to_problem(pyomo.Constraint(index, name=cname, rule=expression))

    def get_dual(self, cname, time=None):
        '''get the dual of a constraint of an LP problem'''
        if user_config.duals:

            return self._parent_problem()._model.dual.get(self.get_constraint(cname, time))

        else:
            return None

    def get_variable(self, name, time=None, indexed=False, scenario=None):
        if indexed:
            var_name = self._id(name)
            if time is None:
                return self._parent_problem().get_component(var_name, scenario)
            else:
                index = str(time)
                return self._parent_problem().get_component(var_name, scenario)[index]
        else:
            var_name = self._t_id(name, time)
            return self._parent_problem().get_component(var_name, scenario)

    def get_constraint(self, name, time):
        return self._parent_problem().get_component(self._t_id(name, time))

    def get_parameter(self, name, time, indexed=False):
        if not indexed:
            return self._parent_problem().get_component(self._t_id(name, time))

        name = self._id(name)
        if time is None:
            return self._parent_problem().get_component(name)
        else:
            return self._parent_problem().get_component(name)[str(time)]

    def add_children(self, objects, name):
        '''Add a child :class:`~optimization.OptimizationObject` to this object.'''
        self.children[name] = objects
        try:
            # if objects is actually a dictionary
            for child in list(self.children[name].values()):
                child._parent_problem = self._parent_problem
        except AttributeError:
            for child in self.children[name]:
                child._parent_problem = self._parent_problem

        setattr(self, name, objects)

    def get_child(self, name, time=None):
        '''
        Get a child :class:`~optimization.OptimizationObject`
        dependent on time from this object.
        '''
        try:
            if time is None:
                return self.children[name]
            else:
                return self.children[name][time]
        except KeyError:
            print(list(self.children.keys()))
            raise

    def iden(self, time):
        '''
        Individual class defined.
        Identifing string for the object, depending on time.
        Used to name variables and constraints for the object.
        '''
        return str(self) + '_' + str(time)

    def _t_id(self, name, time):

        return name.replace(' ', '_') + '_' + self.iden(time)

    def _id(self, name):
        return name.replace(' ', '_') + '_' + str(self)

    def __str__(self):
        '''
        Individual class defined.
        A string representation of the object (used when calling ``print``).
        You probably want to override this one with a more descriptive string.
        '''
        return 'opt_obj{ind}'.format(ind=self.index)

    def _remove_component(self, name, time=None):
        key = self._t_id(name, time)
        delattr(self._parent_problem()._model, key)

    def values(self, name, reindex=None):
        '''return the values of an indexed pyomo component as a Series'''
        print("****", name)
        var = self._parent_problem().get_component(self._id(name))
        out = pd.Series(dict([(k, value(v)) for k, v in list(var.items())]))
        if reindex is not None:
            out.index = reindex
        return out


class OptimizationProblem(OptimizationObject):
    '''an optimization problem/model based on pyomo'''

    def __init__(self):
        self.init_optimization()

    def init_optimization(self):
        self._model = pyomo.ConcreteModel('power system problem')
        self.stochastic_formulation = False
        self.solved = False
        self.children = dict()
        self.variables = dict()
        self.constraints = dict()

    def add_children(self, objL, name):
        '''Add a child :class:`~optimization.OptimizationObject` to this object.'''
        self.children[name] = objL
        setattr(self, name, objL)
        for child in self.children[name]:
            child._parent_problem = weakref.ref(self)

    def add_component_to_problem(self, component):
        '''add a optimization component to the model'''

        if ':' in component.name:
            print("component.name: ", component.name)
            print("component: ", component)
            raise ValueError('no colons allowed in optimization object names')

        self._model.add_component(component.name, component)

    def add_objective(self, expression, sense=pyomo.minimize):
        '''add an objective to the problem'''
        self._model.objective = pyomo.Objective(name='objective', expr=expression, sense=sense)

    def add_set(self, name, items, ordered=False):
        '''add a :class:`pyomo.Set` to the problem'''
        self._model.add_component(name, pyomo.Set(initialize=items, name=name, ordered=ordered))

    def add_variable(self, name, **kwargs):
        '''create a new variable and add it to the root problem'''
        def map_args(kind='Continuous', low=None, high=None):
            return dict(bounds=(low, high), domain=variable_kinds[kind])
        var = pyomo.Var(name=name, **map_args(**kwargs))
        self._model.add_component(name, var)

    def add_constraint(self, name, expression, time=None):
        cname = self._t_id(name, time) if time is not None else name
        self._model.add_component(cname, pyomo.Constraint(name=name, expr=expression))

    def add_suffix(self, name):
        self._model.add_component(name, pyomo.Suffix(direction=pyomo.Suffix.IMPORT))

    def get_component(self, name, scenario=None):
        '''Get an optimization component'''
        if scenario is None:
            try:
                return getattr(self._model, name)
            except (AttributeError, KeyError):
                raise AttributeError('error getting {}'.format(name))
        else:
            try:
                return getattr(self._scenario_instances[scenario], name)
            except AttributeError:
                self._scenario_instances[scenario].pprint()
                raise

    def write_model(self, filename):
        try:
            self._model.write(filename, symbolic_solver_labels=True)
        except:
            self._model.pprint(filename)

    def _remove_component(self, name, time=None):
        key = self._t_id(name, time) if time is not None else name
        delattr(self._model, key)

    def reset_objective(self):
        delattr(self._model, 'objective')

    def reset_model(self):
        instances = [self._model]
        if self.stochastic_formulation:
            instances.append(self._stochastic_instance)

        for instance in instances:

            for pw in list(instance.active_components(pyomo.Piecewise).values()):
                pw._constraints_dict = None
                pw._vars_dict = None
                pw._sets_dict = None

            # another memory leak
            for key, var in list(instance.active_components(pyomo.Param).items()):
                var._index = None
            for key, var in list(instance.active_components(pyomo.Var).items()):
                var.reset()
                var._index = None
                var._data = None

                delattr(instance, key)

        if True and self.stochastic_formulation:
            # destroy scenario tree
            for stage in self._scenario_tree._stages:
                stage._cost_variable = None
                for node in stage._tree_nodes:
                    node._children = None
                    node._parent = None
                    node.__dict__ = {}
                else:
                    stage._tree_nodes = None
            else:
                self._scenario_tree._stages = None

            for scenario in self._scenario_tree._scenarios:
                scenario._leaf_node = None
                scenario._node_list = None
            else:
                self._scenario_tree._scenarios = None

            self._scenario_tree._tree_node_map = None
            self._scenario_tree._tree_nodes = None
            self._scenario_tree = None

            self._stochastic_instance.components._component = {}
            self._stochastic_instance.components._declarations = {}

            self._stochastic_instance = None

        self.solved = False
        self._model = pyomo.ConcreteModel()

    def show_model(self):
        components = self._model.components._component
        items = [pyomo.Set, pyomo.Param, pyomo.Var,
                 pyomo.Objective, pyomo.Constraint]
        for item in items:
            if item not in components:
                continue
            keys = sorted(components[item].keys())
            print(len(keys), item.__name__ + " Declarations")
            for key in keys:
                components[item][key].pprint()
            print("")

    def update_variables(self):
        '''Replace the variables with their numeric value.'''
        for name, var in list(self._model.active_components(pyomo.Var).items()):
            try:
                setattr(self._model, name, value(var))
            except ValueError:
                # for boolean sets this sometimes doesn't work due to rounding
                if var.domain == pyomo.Boolean:
                    setattr(self._model, name, round(value(var)))
                else:
                    raise

    def solve(self):
        '''
        Send the optimization problem off to the solver.
        '''
        solver = user_config.solver
        get_duals = user_config.duals

        # create instance
        if self.stochastic_formulation:
            instance = self._stochastic_instance
        else:
            instance = self._model
            logging.debug('... model created')

        if get_duals:
            logging.debug('... solution loaded')

            # resolve with fixed variables
            logging.info('resolving fixed-integer LP for duals')

            results, elapsed = self._solve_instance(self._model, get_duals=get_duals)
            self.solution_time = elapsed
            instance.solutions.load_from(results)
            logging.debug('... LP problem solved')

        else:
            results, elapsed = self._solve_instance(self._model, solver)

            if self.solved:
                self.solution_time = elapsed

            if user_config.problem_file:
                self.write_model(full_filename('problem.lp'))

            if not self.solved:
                if user_config.problem_file and self.stochastic_formulation:
                    self._stochastic_instance.pprint(full_filename(
                        'unsolved-stochastic-instance.txt'))
                raise OptimizationError('problem not solved')

            instance.solutions.load_from(results)

        logging.debug('... solution loaded')

        if self.stochastic_formulation:
            self._scenario_tree.snapshotSolutionFromInstances(
                self._scenario_instances)

        self.objective = get_objective(results,
                                       name='MASTER' if self.stochastic_formulation else 'objective')

        return self._model

    def __str__(self):
        return 'system'

    def _solve_instance(self, instance,
                        solver=user_config.solver,
                        get_duals=False,
                        keepfiles=False,
                        ):

        if user_config.keep_lp_files:
            keepfiles = True

        suffixes = ['dual'] if get_duals else []

        if not hasattr(self, '_opt_solver'):
            kwds = {}

            self._opt_solver = cooprsolver.SolverFactory(solver, **kwds)
            self._opt_solver.options.mipgap = user_config.mipgap

            if self._opt_solver is None:
                msg = 'solver "{}" not found by coopr'.format(solver)
                raise OptimizationError(msg)

        if user_config.solver_time_limit:
            self._opt_solver.options.timelimit = user_config.solver_time_limit
            print("optimizaton .py", self._opt_solver.options.timelimit)

        # if we are debugging, show the solver output
        show_solver_output = user_config.logging_level <= 10

        start = time.time()

        quiet_fn = not_quiet if keepfiles or show_solver_output else quiet

        with quiet_fn():
            results = self._opt_solver.solve(self._model,
                                             suffixes=suffixes,
                                             keepfiles=keepfiles,
                                             tee=show_solver_output,
                                             load_solutions=False,
                                             )

        print("status ==", results.solver.status, results.solver.termination_condition)

        if (results.solver.termination_condition.value == "infeasible"):
            log_infeasible_constraints(self._model)
            

        if internal_vars.OPF_Price is True:

            LMP = []

            for con in results.solution.constraint:
                for key in results.solution.constraint[con].keys():
                    LMP.append((results.solution.constraint[con][key]))
            bus_name = []
            for c in instance.component_objects(pyomo.Constraint, active=True):
                bus_name.append(str(c))

            Diff = len(LMP) - len(bus_name)
            del LMP[(len(LMP) - 1 - Diff):len(LMP) - 1]

            d = {'bus': bus_name, 'LMP': LMP}
            df = pd.DataFrame(d)

            for index in df.index:
                z = df.ix[index, 'bus']
                w = df.ix[index, 'LMP']
                if (z[0]) != 'p' or (w) < 0.0000001:
                    df = df.drop(index)
            k = len(df.index)
            df.index = range(0, k)
            df.ix[:, 'bus'] = range(1, k + 1)

            if len(df.ix[:, 'bus']) == 0:
                d = {'bus': bus_name, 'LMP': 0}
                df = pd.DataFrame(d)
                for index in df.index:
                    z = df.ix[index, 'bus']
                    if (z[0:15]) != 'power_balance_i':
                        df = df.drop(index)
                k = len(df.index)
                df.index = range(0, k)
                df.ix[:, 'bus'] = range(1, k + 1)

            df.to_csv(FOLDER_PRICE_OPF / 'powerflow-LMP.csv')

        if internal_vars.UC_LMP is True and UC_NETWORKED:

            LMP = []
            for con in results.solution.constraint:
                for key in results.solution.constraint[con].keys():
                    LMP.append((results.solution.constraint[con][key]))

            X = [i for i in LMP if i > 0.0000001]
            bus_name = pd.read_csv(FOLDER_UC / 'Bus_name.csv')

            num_bus = len(bus_name)
            X = X[:num_bus * HOURS_COMMITMENT]

            splited = []
            len_X = len(X)
            for i in range(num_bus):
                start = int(i * len_X / num_bus)
                end = int((i + 1) * len_X / num_bus)
                splited.append(X[start:end])

            LMP = pd.DataFrame(splited, index=[bus_name['bus_name']])

            LMP.to_csv(FOLDER_UC / 'LMP.csv')

        try:
            self._opt_solver._symbol_map = None  # this should mimic the memory leak bugfix at: software.sandia.gov/trac/coopr/changeset/5449
        except AttributeError:
            pass  # should remove after this fix becomes part of a release
        elapsed = (time.time() - start)
        self.solved = detect_status(results, self._opt_solver.name)

        if self.solved:
            try:
                self.mipgap = results.Solution[0]['Gap']
                logging.debug('solution gap={}'.format(self.mipgap))
            except AttributeError:
                self.mipgap = None

        return results, elapsed

    def fix_binary_variables(self, fix_offs=True):
        _fix_binary_variables(
            self._model,
            self.stochastic_formulation,
            fix_offs)

    def _unfix_variables(self):
        _unfix_variables(self._model)

    def _fix_variables(self, names):
        _fix_variables(names, self._model)

    def _remove_all_constraints(self):
        for key in list(self._model.active_components(pyomo.Constraint).keys()):
            delattr(self._model, key)


def _fix_binary_variables(instance, is_stochastic=False, fix_offs=True):
    '''fix binary variables to their solved values to create an LP problem'''
    active_vars = instance.active_components(pyomo.Var)
    for var in active_vars.values():
        if isinstance(var.domain, pyomo.base.IntegerSet) or \
                isinstance(var.domain, pyomo.base.BooleanSet):
            if var.is_indexed():
                for key, ind_var in list(var.items()):
                    if fix_offs or ind_var.value >= 1 - 1e-5:
                        # not quite integer values can create strange
                        # and often infeasible resolve problems
                        ind_var.value = round(ind_var.value)
                        ind_var.fixed = True
            else:
                if fix_offs or var.value == 1:
                    var.fixed = True
    if is_stochastic:
        for scenario_block in [blk for blk in list(instance.active_components(pyomo.Block).values()) if type(blk) != pyomo.Piecewise]:
            _fix_binary_variables(scenario_block)
    # need to preprocess after fixing
    instance.preprocess()


def _fix_variables(names, instance):
    active_vars = instance.active_components(pyomo.Var)
    for var in list(active_vars.values()):
        if var.name in names:
            if var.is_indexed():
                for key, ind_var in list(var.items()):
                    ind_var.fixed = True
            else:
                var.fixed = True
    instance.preprocess()


def _unfix_variables(instance):
    active_vars = instance.active_components(pyomo.Var)
    for var in list(active_vars.values()):
        if var.is_indexed():
            for key, ind_var in list(var.items()):
                ind_var.fixed = False
        else:
            var.fixed = False


def value(variable):
    '''
    Value of an optimization variable after the problem is solved.
    If passed a numeric value, will return the number.
    '''
    try:
        return variable.value
    except AttributeError:
        return variable  # just a number


def detect_status(results, solver):
    '''decide between a solver success or failure'''
    status_text = str(results.solver[0]['Termination condition'])

    if status_text == 'optimal':
        success = True
    elif status_text in ['infeasible', 'unbounded']:
        success = False
    elif status_text in ['unknown', 'maxTimeLimit']:
        # max time limit - the solver may have reached a viable solution
        # unknown - an edge case encountered in resolves
        success = len(list(results.Solution.Variable.keys())) > 0
    else:
        success = False

    if not success:
        logging.critical('problem not solved'
                         + ' - solver terminated with status: "{}"'.format(status_text))
    return success


def get_objective(results, name='objective'):
    value = 0
    try:
        value = []
        for con in results.solution.objective:
            for key in results.solution.objective[con].keys():
                value.append(results.Solution.objective[con][key])
        if len(value) != 0:
            value = value[0]
        else:
            value = 0
    except AttributeError:
        objs = list(results.Solution.objective.values())
        value = [obj['Value'] for obj in objs if 'Value' in obj][0]

    return value


class OptimizationError(Exception):

    '''Error that occurs within solving an optimization problem.'''

    def __init__(self, ivalue):
        if ivalue:
            self.value = ivalue
        else:
            self.value = "Optimization Error: there was a problem"
        Exception.__init__(self, self.value)

    def __str__(self):
        return self.value


class OptimizationResolveError(OptimizationError):

    '''Error that occurs when re-solving an optimization problem.'''
    pass


class NotInModelError(Exception):

    '''
    Error that occurs when trying to find
    a component in the optimization model.
    '''

    def __init__(self, ivalue):
        if ivalue:
            self.value = ivalue
        else:
            self.value = '''the component you are looking
                for wasn't found in the model'''
        Exception.__init__(self, self.value)

    def __str__(self):
        return self.value

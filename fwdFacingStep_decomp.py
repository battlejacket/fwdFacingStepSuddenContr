# Copyright (c) 2023, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# import os
from os import walk, path
import csv

from sympy import Symbol, Eq, Abs, StrictGreaterThan, StrictLessThan, And, tanh, Or, GreaterThan, LessThan, Not, sqrt, lambdify
import numpy as np
import torch

import modulus.sym
from modulus.sym.hydra import to_absolute_path, ModulusConfig
# from modulus.sym.utils.io import csv_to_dict
from modulus.sym.solver import Solver
from modulus.sym.domain import Domain
from modulus.sym.geometry import Parameterization
from modulus.sym.geometry.primitives_2d import Rectangle, Rectangle, Line
from modulus.sym.domain.constraint import (
    PointwiseBoundaryConstraint,
    PointwiseInteriorConstraint,
    IntegralBoundaryConstraint,
)

from modulus.sym.domain.inferencer import PointwiseInferencer
from modulus.sym.domain.monitor import PointwiseMonitor
from modulus.sym.key import Key
from modulus.sym.node import Node
from modulus.sym.eq.pdes.navier_stokes import NavierStokes
from modulus.sym.eq.pdes.basic import NormalDotVec
from modulus.sym.models.fourier_net import FourierNetArch
from modulus.sym.utils.io.vtk import var_to_polyvtk
from ansysValidator import ansysValidator
from dataConstraint import dataConstraint


Re = Symbol("Re")
xPos = Symbol("xPos")
x, y = Symbol("x"), Symbol("y")
Lo, Ho = Symbol("Lo"), Symbol("Ho")
u, v = Symbol("u"), Symbol("v")
vel = Symbol("vel")
p, q = Symbol("p"), Symbol("q")
nu = Symbol("nu")


# add constraints to solver
# specify params
D1 = 1
L1 = 6*D1

# stepRatio = D1-0.66
# stepHeight = stepRatio*D1

stepHeight = 0.5*D1

D2 = D1-stepHeight
L2 = 12*D1

Wo = 0.1

Um = 1
rho = 1

velprof = Um*2*(1-(Abs(y)/(D1/2))**2)
# velprof2 = (4*Um*2/(D1^2))*(D1*(y)-(y)^2)

param_ranges = {
    Re: (100, 1000),
    Lo: (0.1, 1),
    Ho: (0.1, 0.5),
    }

# param_ranges = {
#     Re: (100, 1000),
#     Lo: (0.1, 1),
#     Ho: (0.165, 0.33),
#     } 

# param_ranges = {
#     Re: 500,
#     Lo: 0.5,
#     Ho: 0.165,
#     }



def ffs(designs=[], reynoldsNr=500):

    @modulus.sym.main(config_path="conf", config_name="config")
    def run(cfg: ModulusConfig) -> None:

        pr = Parameterization(param_ranges)

        # make geometry
        pipe1 = Rectangle((-L1, -D1/2), (0, D1/2), parameterization=pr)
        pipe2 = Rectangle((-L1/2, -D1/2), (L2, -D1/2+D2), parameterization=pr)
        
        pipe = pipe1+pipe2

        # inlet = Line((-L1, -D1/2),(-L1, D1/2), parameterization=pr)
        # outlet = Line((L2, D1/2-D2),(L2, D1/2), parameterization=pr)
        inlet = Line((-L1, -D1/2),(-L1, D1/2), parameterization=pr)
        outlet = Line((L2, -D1/2),(L2, -D1/2+D2), parameterization=pr)

        integralPlane = Line((xPos, -D1/2),(xPos, D1/2), parameterization=pr)

        # obstacle = Rectangle((-Lo, -D1/2),(-Lo+Wo, (-D1/2)+Ho), parameterization=pr)
        obstacle = Rectangle((-Lo, D1/2-Ho),(-Lo+Wo, D1/2), parameterization=pr)

        pipe -= obstacle
        
        def interiorCriteria(invar, params):
            sdf = pipe.sdf(invar, params)
            return np.greater(sdf["sdf"], 0)

        # var_to_polyvtk(obstacle.sample_boundary(
        # nr_points=1000, parameterization={Re: (800,800), Lo: (0.3, 0.3), Ho: (0.2, 0.2)}), './vtp/obstacle')
        # var_to_polyvtk(pipe2.sample_boundary(
        # nr_points=1000, parameterization={Re: (800,800), Lo: (0.3, 0.3), Ho: (0.1, 0.1)}), './vtp/pipe2')
        # var_to_polyvtk(pipe.sample_boundary(
        # nr_points=1000, parameterization={Re: (800,800), Lo: (0.3, 0.3), Ho: (0.1, 0.1)}), './vtp/pipe')

        # print("geo done")


        # make annular ring domain
        domain = Domain()

        # make list of nodes to unroll graph on
        input_keys=[Key("x"), Key("y"), Key("Re_norm"), Key("Ho"), Key("Lo")]
        # input_keys=[Key("x"), Key("y")]
        output_keys=[Key("u"), Key("v"), Key("p")]

        ns = NavierStokes(nu=nu, rho=rho, dim=2, time=False)
        # ns_t = NavierStokes_t(nu=nu, rho=rho, dim=2, time=False)
        normal_dot_vel = NormalDotVec(["u", "v"])
        
        input_keys_1=[Key("x_1"), Key("y"), Key("Re_norm"), Key("Ho"), Key("Lo")]
        output_keys_1=[Key("u_1"), Key("v_1"), Key("p_1")]
        
        flow_net_1 = FourierNetArch(
            input_keys=input_keys_1,
            output_keys=output_keys_1,
            frequencies=("axis", [i/2 for i in range(16)]),
            frequencies_params=("axis", [i/2 for i in range(16)]),
            layer_size=256,
            nr_layers=4,
            adaptive_activations=True,
            )
        
        input_keys_2=[Key("x_2"), Key("y"), Key("Re_norm"), Key("Ho"), Key("Lo")]
        output_keys_2=[Key("u_2"), Key("v_2"), Key("p_2")]
        
        flow_net_2 = FourierNetArch(
            input_keys=input_keys_2,
            output_keys=output_keys_2,
            # frequencies=("axis", [i/2 for i in range(16)]),
            # frequencies_params=("axis", [i/2 for i in range(16)]),
            frequencies=("axis", [i/2 for i in range(32)]),
            frequencies_params=("axis", [i/2 for i in range(32)]),
            layer_size=256,
            nr_layers=6,
            adaptive_activations=True,
            )
        
        input_keys_3=[Key("x_3"), Key("y"), Key("Re_norm"), Key("Ho"), Key("Lo")]
        output_keys_3=[Key("u_3"), Key("v_3"), Key("p_3")]
        
        flow_net_3 = FourierNetArch(
            input_keys=input_keys_3,
            output_keys=output_keys_3,
            frequencies=("axis", [i/2 for i in range(16)]),
            frequencies_params=("axis", [i/2 for i in range(16)]),
            layer_size=256,
            nr_layers=4,
            adaptive_activations=True,
            )

            
        limits_x = [-Lo+Wo/2, 0+Wo/2]
        maxLen = 20
        grad = 10
        gradC = 10
        overlap=0.0
        # overlap=0.1
        basis_function_1 = 0.5 * (tanh(grad * (0 + limits_x[0] - x)) + tanh(grad * (x + maxLen + limits_x[0])))
        basis_function_2 = 0.25 * (tanh(gradC * (maxLen - (limits_x[0]-overlap) - x)) + tanh(gradC * (x + 0 - (limits_x[0]-overlap)))) * (tanh(gradC * (0 + (limits_x[1]+overlap) - x)) + tanh(gradC * (x + maxLen + (limits_x[1]+overlap))))
        basis_function_3 = 0.5 * (tanh(grad * (maxLen -limits_x[1] - x)) + tanh(grad * (x + 0 - limits_x[1])))

        # # plot the basis functions for visualization
        # basis_function_1_lf = lambdify(x, basis_function_1, "numpy")
        # basis_function_2_lf = lambdify(x, basis_function_2, "numpy")
        # basis_function_3_lf = lambdify(x, basis_function_3, "numpy")
        # y_vals = np.linspace(-4, 4, 600)

        # out_bf_1 = basis_function_1_lf(y_vals)
        # out_bf_2 = basis_function_2_lf(y_vals)
        # out_bf_3 = basis_function_3_lf(y_vals)

        # plt.figure()
        # plt.plot(y_vals, out_bf_1, label="basis_function_1", color="blue")
        # plt.plot(y_vals, out_bf_2, label="basis_function_2", color="green")
        # plt.plot(y_vals, out_bf_3, label="basis_function_3", color="red")

        # plt.legend()
        # plt.savefig(to_absolute_path("./basis_function_viz.png"))

        merge_nodes = [Node.from_sympy(Symbol("u_1") * 2 * basis_function_1 + Symbol("u_2") * 2 * basis_function_2 + Symbol("u_3") * 4 * basis_function_3, "u")
            ] + [Node.from_sympy(      Symbol("v_1") * -2* basis_function_1 + Symbol("v_2") *-2 * basis_function_2 + Symbol("v_3") * -2* basis_function_3, "v")
            # ] + [Node.from_sympy(      Symbol("v_1") * basis_function_1 + Symbol("v_2") * basis_function_2 + Symbol("v_3") * basis_function_3, "v")
            ] + [Node.from_sympy(      Symbol("p_1") * 20 * basis_function_1 + Symbol("p_2") * 20 * basis_function_2 + Symbol("p_3") * 10 * basis_function_3, "p")
            ]

        nodes = (
            ns.make_nodes()
            # copies[0].make_nodes()
            # + copies[1].make_nodes()
            # + copies[2].make_nodes()
            + merge_nodes
            # + interface_nodes
            + normal_dot_vel.make_nodes()
            # + ns_t.make_nodes()
            + [flow_net_1.make_node(name="flow_network_1", optimize=True)
            ] + [flow_net_2.make_node(name="flow_network_2", optimize=True)
            ] + [flow_net_3.make_node(name="flow_network_3", optimize=True)
            ] + [Node.from_sympy(Re/1000, "Re_norm")
            ] + [Node.from_sympy(rho*Um*D1/Re, "nu")
            ] + [Node.from_sympy(1*u, "u_d")
            ] + [Node.from_sympy(1*v, "v_d")
            ] + [Node.from_sympy(1*p, "p_d")        
            ] + [Node.from_sympy(p+0.5*rho*(sqrt(u**2 + v**2))**2, "ptot")
            ] + [Node.from_sympy((x+Lo)/6, "x_1")
            ] + [Node.from_sympy(1-(x+Lo)/(Lo), "x_2")
            ] + [Node.from_sympy(x/12, "x_3")
            ] + [Node.from_sympy(basis_function_1, "bf1")
            ] + [Node.from_sympy(basis_function_2, "bf2")
            ] + [Node.from_sympy(basis_function_3, "bf3")
            ] + [Node.from_sympy(1*(0.5 + 0.5*tanh(20 * (Symbol("sdf")-0.1))), "lambda_interior")        
            ]
        )


        if cfg.run_mode=="train" and cfg.custom.usePhysics:
            # inlet
            inletConstraint = PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=inlet,
                outvar={"u": velprof, "v": 0},
                batch_size=cfg.batch_size.inlet,
                batch_per_epoch=cfg.batch_size.batchPerEpoch,
                lambda_weighting={"u": 10, "v": 10},
                parameterization=pr,
            )
            domain.add_constraint(inletConstraint, "inlet")

            # outlet
            outletConstraint = PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=outlet,
                outvar={"p": 0},
                batch_size=cfg.batch_size.outlet,
                batch_per_epoch=cfg.batch_size.batchPerEpoch,
                lambda_weighting={"p": 10},
                parameterization=pr,
            )
            domain.add_constraint(outletConstraint, "outlet")

            # no slip
            noSlipCriteria=And(StrictGreaterThan(x, -L1), StrictLessThan(x, L2))
            noSlipHrCriteria=And(GreaterThan(x,-1.5*D1), LessThan(x,D1/2), GreaterThan(y, 0))
            no_slip = PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=pipe,
                outvar={"u": 0, "v": 0},
                batch_size=cfg.batch_size.NoSlip,
                batch_per_epoch=cfg.batch_size.batchPerEpoch,
                lambda_weighting={"u": 10, "v": 10},
                # criteria=noSlipCriteria,
                criteria=And(noSlipCriteria, Not(noSlipHrCriteria)),
                parameterization=pr,
            )
            domain.add_constraint(no_slip, "no_slip")

            no_slipHR = PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=pipe,
                outvar={"u": 0, "v": 0},
                batch_size=cfg.batch_size.NoSlipHR,
                batch_per_epoch=cfg.batch_size.batchPerEpoch,
                lambda_weighting={"u": 50, "v": 50},
                criteria=And(noSlipCriteria, noSlipHrCriteria),
                # criteria=StrictLessThan(y, D1/2),
                parameterization=pr,
            )
            domain.add_constraint(no_slipHR, "no_slipHR")


            # interior
            # lambdafunc=0.5 + 0.5*tanh(20 * Symbol("sdf"))
            offset=0.1
            lambdafunc=1*(0.5 + 0.5*tanh(20 * (Symbol("sdf")-offset)))
            criteriaHR=And(GreaterThan(x,2*-D1), LessThan(x,2*D1))
            interior = PointwiseInteriorConstraint(
                nodes=nodes,
                geometry=pipe,
                outvar={"continuity": 0, "momentum_x": 0, "momentum_y": 0},
                batch_size=cfg.batch_size.Interior,
                lambda_weighting={
                    "continuity": lambdafunc,
                    "momentum_x": lambdafunc,
                    "momentum_y": lambdafunc,
                },
                criteria=Not(criteriaHR),
                batch_per_epoch=cfg.batch_size.batchPerEpoch,
                # quasirandom=True,
                parameterization=pr,
            )
            domain.add_constraint(interior, "interior")
            
            interiorHR = PointwiseInteriorConstraint(
                nodes=nodes,
                geometry=pipe,
                outvar={"continuity": 0, "momentum_x": 0, "momentum_y": 0},
                batch_size=cfg.batch_size.InteriorHR,
                lambda_weighting={
                    "continuity": lambdafunc,
                    "momentum_x": lambdafunc,
                    "momentum_y": lambdafunc,
                },
                criteria=criteriaHR,
                batch_per_epoch=cfg.batch_size.batchPerEpoch,
                parameterization=pr,
            )
            domain.add_constraint(interiorHR, "interiorHR")
            
            # interiorUHR = PointwiseInteriorConstraint(
            #     nodes=nodes,
            #     geometry=pipe,
            #     outvar={"continuity": 0, "momentum_x": 0, "momentum_y": 0},
            #     batch_size=cfg.batch_size.InteriorUHR,
            #     lambda_weighting={
            #         "continuity": lambdafunc,
            #         "momentum_x": lambdafunc,
            #         "momentum_y": lambdafunc,
            #     },
            #     criteria=interiorUHRCriteria,
            #     batch_per_epoch=cfg.batch_size.batchPerEpoch,
            #     parameterization=pr,
            # )
            # domain.add_constraint(interiorUHR, "interiorUHR")

            # integral continuity
            integralLimitsHR = (-2*D1, 2*D1)
            # integralLimitsHR = (-Lo*D1, 0.5*D1)
            integral_continuityHR = IntegralBoundaryConstraint(
                nodes=nodes,
                geometry=integralPlane,
                # geometry=pipe,
                outvar={"normal_dot_vel": Um*2*(1 - (1/3)/(D1**2))},
                batch_size=5,
                integral_batch_size=cfg.batch_size.integralContinuity,
                lambda_weighting={"normal_dot_vel": 0.1},
                parameterization={**param_ranges, **{xPos: integralLimitsHR}},
                fixed_dataset=False,
                criteria=interiorCriteria
            )
            domain.add_constraint(integral_continuityHR, "integral_continuityHR")
            
            integral_continuityUS = IntegralBoundaryConstraint(
                nodes=nodes,
                geometry=integralPlane,
                # geometry=pipe,
                outvar={"normal_dot_vel": Um*2*(1 - (1/3)/(D1**2))},
                batch_size=2,
                integral_batch_size=cfg.batch_size.integralContinuity,
                lambda_weighting={"normal_dot_vel": 0.1},
                parameterization={**param_ranges, **{xPos: (-L1, integralLimitsHR[0])}},
                fixed_dataset=False,
                criteria=interiorCriteria
            )
            domain.add_constraint(integral_continuityUS, "integral_continuityUS")
            
            integral_continuityDS = IntegralBoundaryConstraint(
                nodes=nodes,
                geometry=integralPlane,
                # geometry=pipe,
                outvar={"normal_dot_vel": Um*2*(1 - (1/3)/(D1**2))},
                batch_size=3,
                integral_batch_size=cfg.batch_size.integralContinuity,
                lambda_weighting={"normal_dot_vel": 0.1},
                parameterization={**param_ranges, **{xPos: (integralLimitsHR[1], L2)}},
                fixed_dataset=False,
                criteria=interiorCriteria
            )
            domain.add_constraint(integral_continuityDS, "integral_continuityDS")

        # -----------------------------------------------Data Constraints------------------------------------------------
        if cfg.run_mode=="train" and cfg.custom.useData:
            # ansysVarNames = ("Pressure [ Pa ]", "Velocity u [ m s^-1 ]", "Velocity v [ m s^-1 ]", "X [ m ]", "Y [ m ]")
            ansysVarNames = ("Pressure", "Velocity:0", "Velocity:1", "Points:0", "Points:1")
            modulusVarNames = ("p_d", "u_d", "v_d", "x", "y")
            scales = ((0,1), (0,1), (0,1), (0,1), (-0.5,1))
            additionalConstraints=None #{"continuity": 0, "momentum_x": 0, "momentum_y": 0}

            for root, dirs, files in walk(to_absolute_path("./ansys/data100")):
                for name in files:
                    reNr = float(name.split("_")[1].split(".")[0].replace(",", ".").split("-")[0])
                    # print(reNr)
                    if reNr < 100 or reNr > 1000:
                        continue
                    else:
                        # print(path.join(root, name))
                        file_path = str(path.join(root, name))
                        domain.add_constraint(dataConstraint(file_path, ansysVarNames, modulusVarNames, nodes, scales, batches=cfg.batch_size.batchesData, skiprows=1, param=True, additionalConstraints=additionalConstraints), name)


        
        # ------------------------------------------------Inferencers------------------------------------------------
        if cfg.run_mode!="train":
            inletConstraint = PointwiseBoundaryConstraint(
                nodes=nodes,
                geometry=inlet,
                outvar={"u": 0},
                batch_size=1,
                batch_per_epoch=1,
                lambda_weighting={"u": 1},
                parameterization=pr,
            )
            domain.add_constraint(inletConstraint, "inlet")
        
        quasi = False
        crit = And(GreaterThan(x,-4*D1), LessThan(x,4*D1))
        nrPoints=20000
        # output_names=["u", "v", "p", "nu", "Re", "Lo", "Ho"]
        output_names_decomp=["x_1", "x_2", "x_3", "u_1", "u_2", "u_3", "v_1", "v_2", "v_3", "p_1", "p_2", "p_3", "bf1", "bf2", "bf3"]
        output_names=["u", "v", "p", "continuity", "momentum_x", "momentum_y", "lambda_interior"]
        output_names+=output_names_decomp
        
        para={Re: 200, Lo: 0.4, Ho: 0.4}
        interiorInferencer = PointwiseInferencer(
            nodes=nodes,
            invar=pipe.sample_interior(nr_points=nrPoints, parameterization=para, quasirandom=quasi, criteria=crit),
            output_names=output_names,
            requires_grad=True,
        )
        domain.add_inferencer(interiorInferencer, "interior_" + str(para[Lo]).replace(".", ",") + "_" + str(para[Ho]).replace(".", ",") + "_" + str(para[Re]).replace(".", ","))
        
        
        para={Re: 900, Lo: 1, Ho: 0.4}
        interiorInferencer = PointwiseInferencer(
            nodes=nodes,
            invar=pipe.sample_interior(nr_points=nrPoints, parameterization=para, quasirandom=quasi, criteria=crit),
            output_names=output_names,
            requires_grad=True,
        )
        domain.add_inferencer(interiorInferencer, "interior_" + str(para[Lo]).replace(".", ",") + "_" + str(para[Ho]).replace(".", ",") + "_" + str(para[Re]).replace(".", ","))
        
        
        # noSlipInferencer = PointwiseInferencer(
        #     nodes=nodes,
        #     invar=obstacle.sample_boundary(nr_points=500, parameterization=para, quasirandom=quasi, criteria=crit),
        #     output_names=output_names,
        # )
        # domain.add_inferencer(noSlipInferencer, "noSlip_" + str(para[Lo]).replace(".", ",") + "_" + str(para[Ho]).replace(".", ",") + "_" + str(para[Re]).replace(".", ","))

        #------------------------------------------Validators---------------------------------------------------------
        # ansysVarNames = ("Pressure [ Pa ]", "Velocity u [ m s^-1 ]", "Velocity v [ m s^-1 ]", "X [ m ]", "Y [ m ]")
        ansysVarNames = ["Pressure", "Velocity:0", "Velocity:1", "Points:0", "Points:1"]
        modulusVarNames = ["p", "u", "v", "x", "y"]
        scales = ((0,1), (0,1), (0,1), (0,1), (-0.5,1))
        additionalVariables = None #{"continuity": 0, "momentum_x": 0, "momentum_y": 0}

        for root, dirs, files in walk(to_absolute_path("./ansys/validators")):
            for name in files:
                # print(path.join(root, name))
                file_path = str(path.join(root, name))
                # parameters = name.split("_")[1].split(".")[0].replace(",", ".").split("-")
                # shortName = name.split("_")[0] + "_"
                # for parameter in parameters:
                #     parameterF = round(float(parameter), 3)
                #     shortName += "%.3f" % parameterF + "-"
                domain.add_validator(ansysValidator(file_path=file_path, ansysVarNames=ansysVarNames, modulusVarNames=modulusVarNames, nodes=nodes, scales=scales, skiprows=1, param=True, additionalVariables=additionalVariables), name.split("_")[0])
       
        # file_path=to_absolute_path("./ansys/validators/DP154_482,49999999999994-0,38-0,11000000000000001.csv")
        # name="DP154"
        # domain.add_validator(ansysValidator(file_path=file_path, ansysVarNames=ansysVarNames, modulusVarNames=modulusVarNames, nodes=nodes, scales=scales, skiprows=1, param=True, additionalVariables=additionalVariables), name)

        
        # -----------------------------------------------Monitors-----------------------------------------------
        
        # Single Case Monitors
        # upstreamPressurePoints   = integralPlane.sample_boundary(1024, parameterization={**param_ranges, **{xPos:-4*D1}})
        # downstreamPressurePoints = integralPlane.sample_boundary(1024, criteria=interiorCriteria, parameterization={**param_ranges, **{xPos:4*D1}})


        # upstreamPressure = PointwiseMonitor(
        #     invar=upstreamPressurePoints,
        #     output_names=["p"],
        #     metrics={"upstreamPressure": lambda var: torch.mean(var["p"])},
        #     nodes=nodes,
        # )
        # domain.add_monitor(upstreamPressure)

        # downstreamPressure = PointwiseMonitor(
        #     invar=downstreamPressurePoints,
        #     output_names=["p"],
        #     metrics={"downstreamPressure": lambda var: torch.mean(var["p"])},
        #     nodes=nodes,
        # )
        # domain.add_monitor(downstreamPressure)
        
        # # stop criteria monitors
        # upstreamPoints   = integralPlane.sample_boundary(nrPoints, parameterization={**param_ranges, **{xPos:-4*D1}})
        # stopCriteria = PointwiseMonitor(
        #     invar=upstreamPoints,
        #     output_names=["p"],
        #     metrics={"stopCriteria" + nameString: lambda var: torch.mean(var["p"])},
        #     nodes=nodes,
        # )
        # domain.add_monitor(stopCriteria)
        
        # Pressure Comparison Monitors
        # if cfg.run_mode=="eval":
        if True:
            nrPoints=1024
            ansysFilePath=to_absolute_path("./ansysDPsValidation.csv") #csv exported from ansys DOE/parameters (edited to have column names on first row, including "Name" (dp) in first column)
            
            with open(ansysFilePath, "r") as ansysFile:
                reader = csv.reader(ansysFile, delimiter=",")
                for i, parameters in enumerate(reader):
                    if i != 0:
                        # nameString = "_" + parameters[0].replace(" ", "") + "_" + parameters[1] + "_" + parameters[2] + "_" + parameters[3]
                        
                        shortName = "_" + parameters[0].replace(" ", "") + "_"
                        for parameter in parameters[1:]:
                            parameterF = round(float(parameter), 3)
                            shortName += "%.3f" % parameterF + "-"
                        
                        parameterRange={
                            Re: float(parameters[1]),  
                            Lo: float(parameters[2]),
                            Ho: float(parameters[3]),
                        }
                        upstreamPressurePoints   = integralPlane.sample_boundary(nrPoints, criteria=interiorCriteria, parameterization={**parameterRange, **{xPos:-4*D1}})
                        downstreamPressurePoints = integralPlane.sample_boundary(nrPoints, criteria=interiorCriteria, parameterization={**parameterRange, **{xPos:4*D1}})

                        # var_to_polyvtk(upstreamPressurePoints, './vtp/upstreamPressurePoints')
                        # var_to_polyvtk(downstreamPressurePoints, './vtp/downstreamPressurePoints')
                        
                        # ansysUSP = float(parameters[8])
                        # ansysDSP = float(parameters[6])
                        
                        # print(nameString + " USP ", ansysUSP)
                        # print(nameString + " DSP ", ansysDSP)

                        upstreamPressure = PointwiseMonitor(
                            invar=upstreamPressurePoints,
                            # output_names=["p", "ptot"],
                            # metrics={"upstreamPressure" + nameString: lambda var: torch.mean(var["p"]), "upstreamPressureTot" + nameString: lambda var: torch.mean(var["ptot"])},
                            output_names=["p"],
                            # metrics={"upstreamPressure" + nameString: lambda var: torch.mean(var["p"]), "upstreamPressureDiff" + nameString: lambda var: torch.sub(torch.mean(var["p"]), ansysUSP)},
                            metrics={"upstreamPressure" + shortName + "=" + parameters[6]: lambda var: torch.mean(var["p"])},
                            # metrics={"upstreamPressure" + nameString: lambda var: torch.mean(var["p"])},
                            nodes=nodes,
                        )
                        domain.add_monitor(upstreamPressure)

                        downstreamPressure = PointwiseMonitor(
                            invar=downstreamPressurePoints,
                            # output_names=["p", "ptot"],
                            # metrics={"downstreamPressure" + nameString: lambda var: torch.mean(var["p"]), "downstreamPressureTot" + nameString: lambda var: torch.mean(var["ptot"])},
                            output_names=["p"],
                            # metrics={"downstreamPressure" + nameString: lambda var: torch.mean(var["p"]), "downstreamPressureDiff" + nameString: lambda var: torch.mean(var["p"])-ansysDSP},
                            metrics={"downstreamPressure" + shortName + "=" + parameters[5]: lambda var: torch.mean(var["p"])},
                            # metrics={"downstreamPressure" + nameString: lambda var: torch.mean(var["p"])},
                            nodes=nodes,
                        )
                        domain.add_monitor(downstreamPressure)
                    
        # ---------------------------------------------------Optimization Monitors--------------------------------------------------            
        # nrPoints=1024
        # # print(reynoldsNr)
        # for i, para in enumerate(designs):
        #     parameters={
        #         Re: reynoldsNr,  
        #         Lo: float(para[0]),
        #         Ho: float(para[1]),
        #     }
        #     upstreamPressurePoints   = integralPlane.sample_boundary(nrPoints, parameterization={**parameters, **{xPos:-4*D1}})
        #     downstreamPressurePoints = integralPlane.sample_boundary(nrPoints, criteria=interiorCriteria, parameterization={**parameters, **{xPos:4*D1}})

        #     upstreamPressure = PointwiseMonitor(
        #         invar=upstreamPressurePoints,
        #         # output_names=["p", "ptot"],
        #         output_names=["p"],
        #         # metrics={"upstreamPressure_design_" + str(i): lambda var: torch.mean(var["p"]), "upstreamPressureTot_design_" + str(i): lambda var: torch.mean(var["ptot"])},
        #         metrics={"upstreamPressure_design_" + str(i): lambda var: torch.mean(var["p"])},
        #         nodes=nodes,
        #     )
        #     domain.add_monitor(upstreamPressure)
            
        #     # interiorInferencer = PointwiseInferencer(
        #     #     nodes=nodes,
        #     #     invar=pipe.sample_interior(nr_points=nrPoints, parameterization=parameters, quasirandom=quasi, criteria=crit),
        #     #     output_names=["u", "v", "p"],
        #     # )
        #     # domain.add_inferencer(interiorInferencer, "interior_" + str(parameters[Re]).replace(".", ",") + "_" + str(parameters[Lo]).replace(".", ",") + "_" + str(parameters[Ho]).replace(".", ","))

        #     downstreamPressure = PointwiseMonitor(
        #         invar=downstreamPressurePoints,
        #         # output_names=["p", "ptot"],
        #         output_names=["p"],
        #         # metrics={"downstreamPressure_design_" + str(i): lambda var: torch.mean(var["p"]), "downstreamPressureTot_design_" + str(i): lambda var: torch.mean(var["ptot"])},
        #         metrics={"downstreamPressure_design_" + str(i): lambda var: torch.mean(var["p"])},
        #         nodes=nodes,
        #     )
        #     domain.add_monitor(downstreamPressure)
        
        # make solver
        slv = Solver(cfg, domain)

        # start solver
        slv.solve()


    run()

if __name__ == "__main__": 
    ffs()
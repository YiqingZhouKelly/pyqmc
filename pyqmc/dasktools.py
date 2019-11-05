import os
import pyqmc
import numpy as np
import pandas as pd

os.environ["MKL_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"


def distvmc(
    wf,
    coords,
    accumulators=None,
    nsteps=100,
    hdf_file=None,
    npartitions=None,
    nsteps_per=None,
    client=None,
    verbose=False,
    **kwargs
):
    """ 
    Args: 
    wf: a wave function object

    coords: nconf x nelec x 3 

    nsteps: how many steps to move each walker


    """
    if nsteps_per is None:
        nsteps_per = nsteps

    if hdf_file is not None and 'configs' in hdf_file.keys():
        coords.configs = np.array(hdf_file['configs'])
        if verbose:
            print("Restarted calculation")


    if accumulators is None:
        accumulators = {}
    if npartitions is None:
        npartitions = sum([x for x in client.nthreads().values()])
    allruns = []
    niterations = int(nsteps / nsteps_per)
    coord = coords.split(npartitions)
    alldata = []
    for epoch in range(niterations):
        wfs = []
        thiscoord = []
        for i in range(npartitions):
            wfs.append(wf)
            thiscoord.append(coord[i])
        runs = client.map(
            pyqmc.vmc,
            wfs,
            thiscoord,
            **{"nsteps": nsteps_per, "accumulators": accumulators, "stepoffset": epoch},
            **kwargs
        )
        iterdata = []
        for i, r in enumerate(runs):
            res = r.result()
            iterdata.extend(res[0])
            coord[i] = res[1]

        collected_data=pd.DataFrame(iterdata).groupby("step",as_index=False).apply(lambda x: x.mean()).to_dict("records")
        if verbose:
            print("epoch", epoch, "finished", flush=True)

        coords.join(coord)
        alldata.extend(collected_data)
        for d in collected_data:
            pyqmc.mc.vmc_file(hdf_file, d, kwargs, coords)


    return alldata, coords


def dist_lm_sampler(
    wf, configs, params, pgrad_acc, npartitions=None, client=None, lm_sampler=None
):
    """
    Evaluates accumulator on the same set of configs for correlated sampling of different wave function parameters.  Parallelized with parsl.

    Args:
        wf: wave function object
        configs: (nconf, nelec, 3) array
        params: (nsteps, nparams) array 
            list of arrays of parameters (serialized) at each step
        pgrad_acc: PGradAccumulator 

    kwargs:
        npartitions: number of tasks for parallelization
            divides configs array into npartitions chunks

    Returns:
        data: list of dicts, one dict for each sample
            each dict contains arrays returned from pgrad_acc, weighted by psi**2/psi0**2 
    """
    if lm_sampler is None:
        from pyqmc.linemin import lm_sampler

    if npartitions is None:
        npartitions = sum([x for x in client.nthreads().values()])

    configspart = configs.split(npartitions)
    allruns = []
    for p in range(npartitions):
        allruns.append(client.submit(lm_sampler, wf, configspart[p], params, pgrad_acc))

    stepresults = []
    for r in allruns:
        stepresults.append(r.result())

    keys = stepresults[0][0].keys()
    # This will be a list of dictionaries
    final_results = []
    for p in range(len(params)):
        df = {}
        for k in keys:
            # print(k,flush=True)
            # print(stepresults[0][p][k])
            df[k] = np.concatenate([x[p][k] for x in stepresults], axis=0)
        final_results.append(df)

    return final_results


def line_minimization(*args, client, **kwargs):
    import pyqmc

    if "vmcoptions" not in kwargs:
        kwargs["vmcoptions"] = {}
    if "lmoptions" not in kwargs:
        kwargs["lmoptions"] = {}
    kwargs["vmcoptions"]["client"] = client
    kwargs["lmoptions"]["client"] = client
    return pyqmc.line_minimization(*args, vmc=distvmc, lm=dist_lm_sampler, **kwargs)


def cvmc_optimize(*args, client, **kwargs):
    import pyqmc
    from pyqmc.cvmc import lm_cvmc

    if "vmcoptions" not in kwargs:
        kwargs["vmcoptions"] = {}
    if "lmoptions" not in kwargs:
        kwargs["lmoptions"] = {}
    kwargs["vmcoptions"]["client"] = client
    kwargs["lmoptions"]["client"] = client
    kwargs["lmoptions"]["lm_sampler"] = lm_cvmc
    return pyqmc.cvmc_optimize(*args, vmc=distvmc, lm=dist_lm_sampler, **kwargs)


def distdmc_propagate(wf, configs, weights, *args, client, npartitions=None, **kwargs):
    import pyqmc.dmc

    if npartitions is None:
        npartitions = sum([x for x in client.nthreads().values()])

    coord = configs.split(npartitions)
    weight = np.split(weights, npartitions)
    allruns = []
    for nodeconfigs, nodeweight in zip(coord, weight):
        allruns.append(
            client.submit(
                pyqmc.dmc.dmc_propagate, wf, nodeconfigs, nodeweight, *args, **kwargs
            )
        )

    import pandas as pd

    allresults = [r.result() for r in allruns]
    configs.join([x[1] for x in allresults])
    coordret = configs
    weightret = np.vstack([x[2] for x in allresults])
    df = pd.concat([pd.DataFrame(x[0]) for x in allresults])
    notavg = ["weight", "weightvar", "weightmin", "weightmax", "acceptance", "step"]
    # Here we reweight the averages since each step on each node
    # was done with a different average weight.

    for k in df.keys():
        if k not in notavg:
            df[k] = df[k] * df["weight"]
    df = df.groupby("step").aggregate(np.mean, axis=0).reset_index()
    for k in df.keys():
        if k not in notavg:
            df[k] = df[k] / df["weight"]
    print(df)
    return df, coordret, weightret

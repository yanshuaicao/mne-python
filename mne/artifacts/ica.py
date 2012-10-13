# Authors: Alexandre Gramfort <gramfort@nmr.mgh.harvard.edu>
#          Matti Hamalainen <msh@nmr.mgh.harvard.edu>
#          Denis Engemann <d.engemann@fz-juelich.de>
#
# License: BSD (3-clause)

from inspect import getargspec
import numpy as np
from scipy.stats import kurtosis, skew
from scipy import linalg
from ..cov import compute_whitener


class ICA(object):
    """MEG signal decomposition and denoising workflow

    Paramerters
    -----------
    noise_cov : ndarray
        noise covariance used for whitening
    n_components : integer
        number of components to be extracted. If None, no dimensionality
        reduction will be applied.
    random_state : None | int | instance of np.random.RandomState
        np.random.RandomState to initialize the FastICA estimation.
        As the estimation is non-deterministic it can be useful to
        fix the seed to have reproducible results.

    Attributes
    ----------
    pre_whitener : ndrarray | instance of mne.cov.Covariance
        whiter used for preprocessing
    sorted_by : str
        flag informing about the active
    last_fit : str
        flag informing about which type was last fit.
    ch_names : list-like
        ch_names resulting from initial picking
    """
    def __init__(self, noise_cov=None, n_components=None, random_state=None):
        from sklearn.decomposition import FastICA
        self.noise_cov = noise_cov
        self._fast_ica = FastICA(n_components, random_state=random_state)
        self.n_components = n_components
        self.last_fit = 'unfitted'
        self.sorted_by = 'unsorted'
        self._channs = None
        self._last_sort = [None]

    def __repr__(self):
        out = 'ICA '
        if self.last_fit == 'unfitted':
            msg = '(no decomposition, '
        elif self.last_fit == 'raw':
            msg = '(raw data decomposition, '
        else:
            msg = '(epochs decomposition, '

        out += msg + '%i components' % self.n_components

        if self.sorted_by == 'unsorted':
            sorted_by = self.sorted_by
        else:
            sorted_by = 'sorted by %s' % self.sorted_by
        out += ', %s)' % sorted_by

        return out

    def decompose_raw(self, raw, picks, start=None, stop=None):
        """Run the ica decomposition for raw data

        Paramerters
        -----------
        raw : instance of mne.fiff.Raw
            raw measurments to be decomposed
        start : integer
            starting time slice
        stop : integer
            final time slice
        picks : array-like
            channels to be included.

        Returns
        -------
        self : instance of ICA
            returns the instance for chaining
        """
        print ('\nComputing signal decomposition on raw data.'
               '\n    Please be patient. This may take some time')

        self._sort_idx = (np.arange(self.n_components) if self.n_components
                           is not None else np.arange(picks.shape[0]))

        data = self._get_raw_data(raw, picks, start, stop)

        data, self.pre_whitener = self._pre_whiten(data, picks)

        self._fit_data(data)
        self.last_fit = 'raw'
        self._channs = np.array(raw.ch_names)[picks]

        return self

    def decompose_epochs(self, epochs, picks=None):
        """Run the ica decomposition for epochs

        Paramerters
        -----------
        epochs : instance of Epochs
            The epochs. The ICA is estimated on the concatenated epochs.
        picks : array-like
            channels to be included.

        Returns
        -------
        self : instance of ICA
            returns the instance for chaining
        """
        data = self._get_epochs_data(epochs)  # TODO preload
        if picks is None:
            picks = epochs.picks
        self._sort_idx = (np.arange(self.n_components) if self.n_components
                           is not None else np.arange(self.picks.shape[0]))

        print ('\nComputing signal decomposition on epochs.'
               '\n    Please be patient. This may take some time')

        data, self.pre_whitener = self._pre_whiten(data, picks=epochs.picks)
        self._fit_data(data)
        self.last_fit = 'epochs'
        self._channs = np.array(epochs.ch_names)[picks]

        return self

    def get_sources_raw(self, raw, picks='previous', start=None, stop=None,
                        sort_method='skew'):
        """ Uncover raw sources
        Paramerters
        -----------
        raw : instance of Raw
            Raw object to draw sources from
        start : integer
            starting time slice
        stop : integer
            final time slice
        picks : array-like
            channels to be included
        sort_method : str | function
            method used for sorting the sources. Options are 'skew',
            'kurtosis', 'unsorted' or a custom function that takes an
            array and an axis argument.
        """
        if self.last_fit is 'unfitted':
            print ('No fit availble. Please first fit ica decomposition.')
            return

        picks = self._check_picks(raw, picks)
        data = self._get_raw_data(raw, picks, start, stop)
        whitened, _ = self._pre_whiten(data, picks)
        raw_sources = self._get_sources(whitened)

        return self.sort_sources(raw_sources, sort_method=sort_method)

    def get_sources_epochs(self, epochs, picks='previous', sort_method='skew'):
        """ Uncover raw sources
        Paramerters
        -----------
        raw : instance of Raw
            Raw object to draw sources from
        start : integer
            starting time slice
        stop : integer
            final time slice
        picks : array-like
            channels to be included
        sort_method : str | function
            method used for sorting the sources. Options are 'skew',
            'kurtosis', 'unsorted' or a custom function that takes an
            array and an axis argument.

        """
        if self.last_fit is 'unfitted':
            print ('No fit availble. Please first fit ica decomposition.')
            return

        picks = self._check_picks(epochs, picks)
        data = self._get_raw_data(epochs, picks)
        whitened, _ = self._pre_whiten(data, picks)
        epochs_sources = self._get_sources(whitened)

        return self.sort_sources(epochs_sources, sort_method=sort_method)

    def pick_sources_raw(self, raw, bads=[], picks='previous', start=None, stop=None,
                         copy=True, sort_method='skew'):
        """Recompose raw data

        Paramerters
        -----------
        raw : instance of Raw
            raw object to pick to remove ica components from
        bads : list-like
            Indices for transient component deselection
        picks : array-like
            use channel subset as specified
        copy: boolean
            modify raw instance in place or return modified copy
        sort_method : str | function
            method used for sorting the sources. Options are 'skew',
            'kurtosis', 'unsorted' or a custom function that takes an
            array and an axis argument.

        Returns
        -------
        raw : instance of Raw
            raw instance with selected ica components removed
        """
        if self.last_fit != 'raw':
            raise ValueError('Currently no raw data fitted.'
                             'Please fit raw data first.')

        picks = self._check_picks(raw, picks)
        sources = self.get_sources_raw(raw, picks=picks, start=start,
                                       stop=stop, sort_method=sort_method)
        if self._last_sort[0] not in (None, sort_method):
            print ('\n    Sort method demanded is different from last sort'
                   '\n    ... reordering the sources accorodingly')
            sort_func = self._get_sort_method(sort_method)
            sources = np.argsort(sort_func(sources, 1))

        recomposed = self._pick_sources(sources, bads, picks)
        if copy is True:
            raw = raw.copy()

        raw[picks, start:stop] = recomposed

        return raw

    def pick_sources_epochs(self, epochs, bads=[], picks='previous', copy=True,
                            sort_method='skew'):
        """Recompose epochs

        Paramerters
        -----------
        epochs : instance of Epochs
            epochs object to pick to remove ica components from
        bads : list-like
            Indices for transient component deselection
        copy : boolean
            Either return denoised data as nd array or newly instantiated
            Epochs object.
        sort_method : str | function
            method used for sorting the sources. Options are 'skew',
            'kurtosis', 'unsorted' or a custom function that takes an
            array and an axis argument.

        Returns
        -------
        denoised : depends on input arguments
            denoised raw data as ndarray or as instance of Raw
        """

        if picks == None:
            picks = epochs.picks
        elif picks == 'previous':
            picks = self._check_picks(epochs, picks)

        if self.last_fit != 'epochs':
            raise ValueError('Currently no epochs fitted.'
                             'Please fit epochs first.')

        sources = self.get_sources_epochs(epochs)

        if self._last_sort[0] not in (None, sort_method):
            print ('\n    Sort method demanded is different from last sort'
                   '\n    ... reordering the sources accorodingly')
            sort_func = self._get_sort_method(sort_method)
            sources = np.argsort(sort_func(sources, 1))

        recomposed = self._pick_sources(sources, bads, picks)

        if copy is True:
            epochs = epochs.copy()

        epochs._data = recomposed
        epochs._preload = True

        return epochs

    def sort_sources(self, sources, sort_method='skew'):
        """Sort sources accoroding to criteria such as skewness or kurtosis

        Paramerters
        -----------
        sources : str
            string for selecting the sources
        sort_method : str | function
            method used for sorting the sources. Options are 'skew',
            'kurtosis', 'unsorted' or a custom function that takes an
            array and an axis argument.
        """
        if sources.shape[0] != self.n_components:
            raise ValueError('Sources have to match the number of components')

        if self.last_fit is 'unfitted':
            print ('No fit availble. Please first fit ica decomposition.')
            return

        sort_func = self._get_sort_method(sort_method)

        sort_args = np.argsort(sort_func(sources, 1))
        self._sort_idx = self._sort_idx[sort_args]
        self.sorted_by = sort_func.__name__
        # append to sort buffer
        self._last_sort.append(self.sorted_by)
        self._last_sort.pop(0)  # remove last sort
        print '\n    sources reordered by %s' % self.sorted_by

        return sources[sort_args]

    def _check_picks(self, pickable, picks):
        """Helper function"""
        out = None
        if picks is not None:
            if picks is 'previous':
                out = np.in1d(np.array(pickable.ch_names), self._channs)
            elif np.array(pickable.ch_names)[picks].tolist() != self.ch_names:
                raise ValueError('Channel picks have to match '
                                 'the previous fit.')
            else:
                out = picks
        return out

    def _pre_whiten(self, data, picks):
        """Helper function"""
        if self.noise_cov is not None:
            assert data.shape[0] == self.noise_cov.data.shape[0]
            pre_whitener, _ = compute_whitener(self.noise_cov, self.raw.info,
                                               picks)
            data = np.dot(pre_whitener, data)

        elif self.noise_cov is None:  # use standardization as whitener
            std_chan = np.std(data, axis=1) ** -1
            pre_whitener = np.array([std_chan]).T
            data *= pre_whitener
        else:
            raise ValueError('This is not a valid valur for noise_cov')

        return data, pre_whitener

    def _get_raw_data(self, raw, picks, start, stop):
        """Helper function"""
        start = 0 if start is None else start
        stop = raw.last_samp if stop is None else stop
        return raw[picks, start:stop][0]

    def _get_epochs_data(self, epochs):
        """Helper function"""
        data = epochs._data if epochs.preloaded else epochs.get_data()
        return np.hstack(data)

    def _fit_data(self, data):
        """Helper function"""
        self._fast_ica.fit(data.T)
        self.mixing = self._fast_ica.get_mixing_matrix().T

    def _get_sources(self, data):
        """ Helper function """
        sources = self._fast_ica.transform(data.T).T
        return sources

    def _pick_sources(self, sources, bads, picks):
        """Helper function"""

        mixing = self.mixing.copy()
        pre_whitener = self.pre_whitener.copy()
        if self.noise_cov is None:  # revert standardization
            pre_whitener **= -1
            mixing *= pre_whitener.T
        else:
            mixing = np.dot(mixing, linalg.pinv(pre_whitener))

        if bads not in (None, []):
            sources[bads, :] = 0.

        out = np.dot(sources.T, mixing).T

        return out

    def _get_sort_method(self, sort_method):
        """Helper function"""
        if sort_method == 'skew':
            sort_func = skew
        elif sort_method == 'kurtosis':
            sort_func = kurtosis
        elif sort_method == 'unsorted':
            sort_func = lambda x, y: self.source_ids
            sort_func.__name__ = 'unsorted'
        elif callable(sort_method):
            args = getargspec(sort_method).args
            if len(args) > 1:
                if args[:2] == ['a', 'axis']:
                    sort_func = sort_method
            else:
                ValueError('%s is not a valid function.'
                           'The function needs an array and'
                           'an axis argument' % sort_method.__name__)
        elif isinstance(sort_method, str):
            ValueError('%s is not a valid sorting option' % sort_method)

        return sort_func

    @property
    def ch_names(self):
        """ Channel names information"""
        if self._channs is None:
            return 'No channel names specified'
        else:
            return self._channs.tolist()